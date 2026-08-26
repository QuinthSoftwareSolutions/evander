from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evander_team_addon.models import (
    AgentInboxItem,
    AgentRuntime,
    AgentTask,
    DependencyCompositionManifest,
    DependencyGitSource,
    Deliverable,
    GitWorkContext,
    GitWorkContextState,
    HumanReviewFinding,
    HumanReviewRequest,
    HumanReviewRequirementSnapshot,
    ProjectGitConfig,
    ProjectGoal,
    ReleasePackage,
    ReviewBlockKind,
    ReviewRepositorySnapshot,
    ReviewRequest,
    RolePermission,
    TeamBlockerEventType,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamCase,
    TeamCaseStatus,
    TeamDispatch,
    TeamDispatchActivity,
    TeamDispatchActivityStatus,
    TeamDispatchStatus,
    TeamInboxItemType,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamRecordState,
    TaskDeliverableContract,
    TaskMutationPolicy,
    TeamTaskState,
    TeamTaskKind,
    runtime_operations_payload,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.agent_action_service import TeamAgentActionService
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import (
    TeamChatService,
    project_scoped_team_chat_authorization,
)
from evander_team_addon.services.commands import (
    TeamAgentControlCommandService,
    TeamBlockerCommandService,
    TeamCaseCommandService,
    TeamDeliverableSubmissionCommandService,
    TeamMemoryCommandService,
    TeamNoteCommandService,
    TeamOutcomeCatalogCommandService,
    TeamReviewCommandService,
    TeamTaskCreationCommandService,
    TeamTaskUpdateCommandService,
)
from evander_team_addon.services.domain.blockers import (
    open_or_get_blocker,
    resolve_blocker,
    task_cancellation_blocking_handoff_ids,
)
from evander_team_addon.services.domain.task_assessment import assess_task
from evander_team_addon.services.domain.wake import AGENT_WAKE_REQUESTED_EVENT_TYPE
from evander_team_addon.services.domain.work_due import _mark_agent_work_due
from evander_team_addon.services.runtime_service import TeamRuntimeService


MANAGED_REPO_ID = "mr_0123456789abcdefabcd"
LEASE_NOW = datetime(2098, 1, 1, tzinfo=UTC)


def _authorized_chat_mutation(repository: TeamStateRepository, agent_id: str):
    state = repository.read()
    return project_scoped_team_chat_authorization(
        state,
        agent=state.agents[agent_id],
        source="internal",
        principal_id="operator@example.com",
        recipient_directory_path="root",
        project_is_authorized=lambda _directory_path: True,
    )


def _begin_project_chat_dispatch(
    repository: TeamStateRepository,
    *,
    agent_id: str,
    body: str,
) -> None:
    chat = TeamChatService(repository)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id=agent_id,
        operator_user_id="operator@example.com",
        created_by="operator:operator@example.com",
        expected_revision=repository.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body=body,
        created_by="operator:operator@example.com",
        mutation_authorization=_authorized_chat_mutation(repository, agent_id),
        expected_revision=state.revision,
    )
    state, _message = chat.begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )
    dispatch = state.dispatches[f"team-chat:{message.id}"]
    assert dispatch.project_ids == ["project"]


def _begin_project_lead_chat_dispatch(
    repository: TeamStateRepository,
    *,
    body: str,
) -> None:
    _begin_project_chat_dispatch(
        repository,
        agent_id="lead",
        body=body,
    )


def _queue_delivered_operator_attention(
    repository: TeamStateRepository,
    *,
    agent_id: str,
    active_dispatch_id: str,
) -> str:
    chat = TeamChatService(repository)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id=agent_id,
        operator_user_id="operator@example.com",
        expected_revision=repository.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Preserve this attention across abnormal dispatch release.",
        mutation_authorization=_authorized_chat_mutation(repository, agent_id),
        expected_revision=state.revision,
    )
    chat.queue_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )

    def _mark_delivered(state_model):
        delivered = state_model.conversation_messages[message.id]
        delivered.status = "delivered_in_dispatch"
        delivered.seen_at = LEASE_NOW
        delivered.seen_dispatch_id = active_dispatch_id
        delivered.metadata["operator_steering"] = {
            "dispatch_id": active_dispatch_id,
            "state": "delivered",
        }
        return state_model

    repository.update(_mark_delivered)
    return message.id


def _seed_agent_with_task(tmp_path: Path) -> TeamStateRepository:
    repository = TeamStateRepository(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=repository.read().revision,
    )
    admin.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        git=ProjectGitConfig(
            managed_repo_id=MANAGED_REPO_ID,
            base_branch="main",
        ),
        expected_revision=repository.read().revision,
    )
    admin.create_role(
        role_id="project__worker",
        team_id="team",
        project_id="project",
        name="Worker",
        expected_revision=repository.read().revision,
    )
    admin.register_agent(
        agent_id="worker",
        team_id="team",
        project_id="project",
        role_id="project__worker",
        session_id="session-worker",
        reports_to_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="worker",
        project_id="project",
        priority=1,
        title="Scoped task",
        description="Do the scoped work.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    return repository


def test_non_git_project_chat_uses_provider_neutral_directory_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    directory_path = "root/departments/accounting"

    def _make_project_non_git(state):
        state.projects["project"].directory_path = directory_path
        state.projects["project"].git = None
        return state

    state = repository.update(_make_project_non_git)
    agent = state.agents["worker"]
    checked_paths: list[str] = []
    authorized = project_scoped_team_chat_authorization(
        state,
        agent=agent,
        source="internal",
        principal_id="operator@example.com",
        recipient_directory_path=directory_path,
        project_is_authorized=lambda value: checked_paths.append(value)
        or value == directory_path,
    )
    root_only = project_scoped_team_chat_authorization(
        state,
        agent=agent,
        source="internal",
        principal_id="operator@example.com",
        recipient_directory_path=directory_path,
        project_is_authorized=lambda value: value == "root",
    )

    assert checked_paths == [directory_path]
    assert authorized.mode == "project_scoped"
    assert authorized.project_ids == ["project"]
    assert authorized.managed_repo_ids == []
    assert root_only.mode == "reply_only"
    assert root_only.reason == "no_authorized_projects"


def _seed_plain_cancelled_task_with_stranded_dependent(
    repository: TeamStateRepository,
) -> None:
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="successor",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Finished replacement",
        description="This completed task is the recorded successor.",
        created_by_agent_id="lead",
        state=TeamTaskState.FINISHED,
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="dependent",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Stranded downstream work",
        description="This edge predates audited cancellation repair.",
        created_by_agent_id="lead",
        dependency_task_ids=["task"],
        expected_revision=repository.read().revision,
    )
    admin.cancel_task(
        task_id="task",
        reason_code="replaced",
        reason="The successor owns the remaining scope.",
        actor_id="operator:test",
        successor_task_id="successor",
        expected_revision=repository.read().revision,
    )


def _deferred_dispatch(
    repository: TeamStateRepository,
) -> tuple[TeamRuntimeService, str]:
    runtime = TeamRuntimeService(repository)
    _state, leases = runtime.lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
        defer_activation=True,
    )
    assert len(leases) == 1
    return runtime, str(leases[0]["dispatch_id"])


def _seed_secondary_project_for_lead(repository: TeamStateRepository) -> None:
    admin = TeamAdminService(repository)
    admin.create_project(
        project_id="other-project",
        team_id="team",
        name="Other Project",
        lead_agent_id="lead",
        lead_session_id=None,
        lead_role_id="other-project__project-lead",
        goals=[ProjectGoal(id="other-goal", description="Ship other work")],
        expected_revision=repository.read().revision,
    )
    admin.update_role(
        role_id="other-project__project-lead",
        permissions=[
            RolePermission.CREATE_ROOT_TASK,
            RolePermission.MANAGE_OUTCOME_KINDS,
        ],
        expected_revision=repository.read().revision,
    )


def _replace_active_dispatch_project_scope(
    repository: TeamStateRepository,
    *,
    agent_id: str,
    project_ids: list[str],
) -> None:
    def _replace_scope(state):
        dispatch_id = state.agent_runtimes[agent_id].active_dispatch_id
        assert dispatch_id is not None
        state.dispatches[dispatch_id].project_ids = project_ids
        return state

    repository.update(_replace_scope)


def _seed_forged_cross_team_manager_dispatch(
    repository: TeamStateRepository,
    *,
    cause_kind: str = "scheduled",
) -> None:
    admin = TeamAdminService(repository)
    admin.create_team(
        team_id="foreign-team",
        name="Foreign Team",
        manager_agent_id="foreign-manager",
        manager_session_id="session-foreign-manager",
        expected_revision=repository.read().revision,
    )

    def _add_dispatch(state):
        dispatch_id = f"forged-cross-team-{cause_kind}-project-dispatch"
        expires_at = LEASE_NOW + timedelta(hours=1)
        state.dispatches[dispatch_id] = TeamDispatch(
            id=dispatch_id,
            agent_id="foreign-manager",
            session_id="session-foreign-manager",
            cause_kind=cause_kind,
            project_ids=["project"],
            managed_repo_ids=[MANAGED_REPO_ID],
            status=TeamDispatchStatus.LEASED,
            prepared_at=LEASE_NOW,
            leased_at=LEASE_NOW,
            lease_expires_at=expires_at,
        )
        runtime = state.agent_runtimes.setdefault(
            "foreign-manager",
            AgentRuntime(agent_id="foreign-manager"),
        )
        runtime.active_dispatch_id = dispatch_id
        runtime.wake_reason = cause_kind
        runtime.last_wake_at = LEASE_NOW
        runtime.wake_leased_until = expires_at
        runtime.updated_at = LEASE_NOW
        return state

    repository.update(_add_dispatch)


def _seed_cross_team_case_command_targets(
    repository: TeamStateRepository,
) -> None:
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
            completed_at=LEASE_NOW,
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
            task_ids=["human-correction"],
        )
        state.tasks["human-correction"] = AgentTask(
            id="human-correction",
            owner_agent_id="lead",
            project_id="project",
            case_id="human-case",
            priority=0,
            state=TeamTaskState.PENDING,
            task_kind=TeamTaskKind.SUPPORT,
            title="Correct a human review finding",
            description="This correction belongs to the target project's Team.",
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

    repository.update(_seed)


def _lease_lead_for_primary_project(repository: TeamStateRepository) -> None:
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="lead-task",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Lead work",
        description="This dispatch is scoped only to the primary project.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    _state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="lead",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )
    assert len(leases) == 1
    assert leases[0]["dispatch"]["project_ids"] == ["project"]


@pytest.mark.parametrize(
    "status",
    [
        TeamDispatchStatus.COMPLETED,
        TeamDispatchStatus.EXPIRED,
        TeamDispatchStatus.FAILED,
    ],
)
def test_terminal_dispatch_keeps_historical_session_after_agent_rebind(
    tmp_path: Path,
    status: TeamDispatchStatus,
) -> None:
    repository = _seed_agent_with_task(tmp_path)

    def _add_terminal_dispatch(state):
        state.dispatches[f"dispatch-{status.value}"] = TeamDispatch(
            id=f"dispatch-{status.value}",
            agent_id="worker",
            session_id="session-worker",
            cause_kind="scheduled",
            status=status,
            completed_at=LEASE_NOW,
            error="dispatch failed" if status == TeamDispatchStatus.FAILED else None,
        )
        return state

    repository.update(_add_terminal_dispatch)
    state = TeamAdminService(repository).update_agent(
        agent_id="worker",
        session_id="session-worker-next",
        expected_revision=repository.read().revision,
    )

    assert state.agents["worker"].session_id == "session-worker-next"
    assert state.dispatches[f"dispatch-{status.value}"].session_id == "session-worker"


def test_preparing_dispatch_prevents_agent_session_rebind(tmp_path: Path) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _runtime, dispatch_id = _deferred_dispatch(repository)

    with pytest.raises(ValueError, match="session does not match agent"):
        TeamAdminService(repository).update_agent(
            agent_id="worker",
            session_id="session-worker-next",
            expected_revision=repository.read().revision,
        )

    assert repository.read().dispatches[dispatch_id].status == TeamDispatchStatus.PREPARING
    assert repository.read().agents["worker"].session_id == "session-worker"


def test_preparing_dispatch_rejects_commands_and_scope_does_not_widen(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    runtime, dispatch_id = _deferred_dispatch(repository)

    preparing = repository.read().dispatches[dispatch_id]
    assert preparing.status == TeamDispatchStatus.PREPARING
    assert preparing.task_ids == ["task"]
    assert preparing.project_ids == ["project"]
    assert preparing.managed_repo_ids == [MANAGED_REPO_ID]

    _state, rejected = TeamNoteCommandService(repository).record_team_note_command(
        session_id="session-worker",
        body="This must wait until preparation completes.",
        request_id="preparing-note",
    )
    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "dispatch_not_leased"

    runtime.mark_agent_dispatch_leased(
        agent_id="worker",
        dispatch_id=dispatch_id,
        now=LEASE_NOW,
    )
    TeamAdminService(repository).create_or_reprioritize_task(
        task_id="later-task",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Later task",
        description="Created after the dispatch was computed.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )

    leased = repository.read().dispatches[dispatch_id]
    assert leased.status == TeamDispatchStatus.LEASED
    assert leased.task_ids == ["task"]

    _state, out_of_scope = TeamTaskUpdateCommandService(repository).update_task_command(
        session_id="session-worker",
        task_id="later-task",
        request_id="later-task-update",
        state=TeamTaskState.IN_PROGRESS.value,
    )
    assert out_of_scope["success"] is False
    assert out_of_scope["errors"][0]["code"] == "dispatch_scope_forbidden"

    _state, in_scope = TeamTaskUpdateCommandService(repository).update_task_command(
        session_id="session-worker",
        task_id="task",
        request_id="scoped-task-update",
        state=TeamTaskState.IN_PROGRESS.value,
    )
    assert in_scope["success"] is True


def test_immediate_inbox_cause_does_not_narrow_agent_wide_task_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="second-task",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Second actionable task",
        description="This remains in scope even when another record causes the wake.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )

    def _add_immediate_inbox(state):
        state.inbox["inbox-cause"] = AgentInboxItem(
            id="inbox-cause",
            recipient_agent_id="worker",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            task_id="task",
            payload={"question": "Please report the first task status."},
        )
        return state

    repository.update(_add_immediate_inbox)

    state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )

    assert len(leases) == 1
    lease = leases[0]
    assert lease["reason"] == "inbox"
    assert lease["dispatch"]["cause_id"] == "inbox-cause"
    assert lease["dispatch"]["inbox_item_ids"] == ["inbox-cause"]
    assert lease["dispatch"]["task_ids"] == ["second-task", "task"]
    assert {task["id"] for task in lease["tasks"]} == {"task", "second-task"}
    assert state.dispatches[lease["dispatch_id"]].task_ids == [
        "second-task",
        "task",
    ]


def test_review_dispatch_leases_one_snapshot_and_immediately_wakes_for_next(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    TeamAdminService(repository).create_or_reprioritize_task(
        task_id="second-task",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Second review task",
        description="Keep the second immutable review out of the first dispatch.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )

    def _seed_reviews(state):
        review_rows = (
            ("first", "task", "a" * 40, LEASE_NOW - timedelta(minutes=2)),
            ("second", "second-task", "b" * 40, LEASE_NOW - timedelta(minutes=1)),
        )
        for suffix, task_id, commit_sha, created_at in review_rows:
            deliverable_id = f"deliverable-{suffix}"
            review_id = f"review-{suffix}"
            state.tasks[task_id].state = TeamTaskState.WAITING_FOR_REVIEW
            state.tasks[task_id].state_entered_at = created_at
            state.deliverables[deliverable_id] = Deliverable(
                id=deliverable_id,
                task_id=task_id,
                producer_agent_id="lead",
                summary=f"{suffix.title()} immutable review candidate.",
                review_request_ids=[review_id],
                created_at=created_at,
            )
            state.tasks[task_id].deliverable_ids = [deliverable_id]
            state.review_requests[review_id] = ReviewRequest(
                id=review_id,
                deliverable_id=deliverable_id,
                reviewer_agent_id="worker",
                requested_by_agent_id="lead",
                created_at=created_at,
                repository_snapshot=ReviewRepositorySnapshot(
                    managed_repo_id=MANAGED_REPO_ID,
                    directory_path="root",
                    commit_sha=commit_sha,
                    pinned_ref=f"refs/evander/team-reviews/{review_id}",
                    source_kind="deliverable_candidate",
                ),
            )
        state.inbox["inbox-first-review"] = AgentInboxItem(
            id="inbox-first-review",
            recipient_agent_id="worker",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            task_id="task",
            payload={"review_request_id": "review-first"},
            created_at=LEASE_NOW - timedelta(minutes=2),
            updated_at=LEASE_NOW - timedelta(minutes=2),
        )
        state.inbox["inbox-second-review"] = AgentInboxItem(
            id="inbox-second-review",
            recipient_agent_id="worker",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            task_id="second-task",
            payload={"review_request_id": "review-second"},
            response_requested=False,
            priority="low",
            created_at=LEASE_NOW - timedelta(minutes=1),
            updated_at=LEASE_NOW - timedelta(minutes=1),
        )
        return state

    repository.update(_seed_reviews)
    runtime = TeamRuntimeService(repository)
    _state, leases = runtime.lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )

    assert len(leases) == 1
    first_lease = leases[0]
    assert first_lease["dispatch"]["review_request_ids"] == ["review-first"]
    assert first_lease["dispatch"]["inbox_item_ids"] == ["inbox-first-review"]
    assert [review["id"] for review in first_lease["reviews"]] == ["review-first"]
    first_dispatch_id = first_lease["dispatch_id"]

    def _complete_first_review(state):
        state.review_requests["review-first"].state = TeamRecordState.ACCEPTED
        state.review_requests["review-first"].decision_at = LEASE_NOW + timedelta(seconds=1)
        state.inbox["inbox-first-review"].state = TeamInboxItemState.HANDLED
        state.inbox["inbox-first-review"].updated_at = LEASE_NOW + timedelta(seconds=1)
        return state

    repository.update(_complete_first_review)
    repository.append_dispatch_activity(
        TeamDispatchActivity(
            id="activity-first-review-decision",
            tool_name="team_decide_review",
            caller_session_id="session-worker",
            caller_identity="agent:worker",
            agent_id="worker",
            dispatch_id=first_dispatch_id,
            status=TeamDispatchActivityStatus.SUCCESS,
            progress_classification="review_decision",
            created_at=LEASE_NOW + timedelta(seconds=1),
            updated_at=LEASE_NOW + timedelta(seconds=1),
        )
    )
    _state, finalized = runtime.finalize_agent_dispatch_from_activity(
        session_id="session-worker",
        dispatch_id=first_dispatch_id,
    )

    review_wakes = [
        event
        for event in finalized["events"]
        if event["event_type"] == AGENT_WAKE_REQUESTED_EVENT_TYPE
    ]
    assert len(review_wakes) == 1
    assert review_wakes[0]["payload"]["reason"] == "review"
    assert review_wakes[0]["delay_seconds"] == 1.0
    wake_outbox = repository.list_outbox_records(
        intent_type=AGENT_WAKE_REQUESTED_EVENT_TYPE,
    )
    assert len(wake_outbox) == 1
    assert wake_outbox[0].payload["payload"]["reason"] == "review"

    _state, next_leases = runtime.lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW + timedelta(seconds=2),
        respect_wake_interval=False,
    )
    assert len(next_leases) == 1
    assert next_leases[0]["reason"] == "review"
    assert next_leases[0]["dispatch"]["review_request_ids"] == ["review-second"]
    assert next_leases[0]["dispatch"]["inbox_item_ids"] == ["inbox-second-review"]
    assert [review["id"] for review in next_leases[0]["reviews"]] == ["review-second"]


def test_assigned_blocker_recovery_task_is_in_lease_task_payload(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)

    def _block_task_for_recovery(state):
        task = state.tasks["task"]
        task.state = TeamTaskState.BLOCKED
        task.state_entered_at = LEASE_NOW
        open_or_get_blocker(
            state,
            source_kind=TeamBlockerSourceKind.TASK,
            source_id=task.id,
            summary="Recover the blocked task through its assigned owner.",
            reporter_actor="operator@example.com",
            initial_owner_agent_id="worker",
            route_initial_owner_even_if_unavailable=True,
            wake_owner=False,
            team_id="team",
            project_id="project",
            task_id=task.id,
            affected_task_ids=[task.id],
            now=LEASE_NOW,
        )
        return state

    repository.update(_block_task_for_recovery)

    state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )

    assert len(leases) == 1
    lease = leases[0]
    assert lease["dispatch"]["task_ids"] == ["task"]
    assert [task["id"] for task in lease["tasks"]] == ["task"]
    assert lease["tasks"][0]["state"] == TeamTaskState.BLOCKED.value
    assert state.dispatches[lease["dispatch_id"]].task_ids == ["task"]


def test_origin_provenance_does_not_grant_root_authority_or_gate_resolution(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_project(
        project_id="other-project",
        team_id="team",
        name="Other Project",
        lead_agent_id="worker",
        lead_session_id=None,
        goals=[ProjectGoal(id="other-goal", description="Ship other work")],
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="other-origin",
        owner_agent_id="worker",
        project_id="other-project",
        priority=1,
        title="Unrelated origin",
        description="This task cannot be provenance for work in another project.",
        created_by_agent_id="worker",
        expected_revision=repository.read().revision,
    )
    runtime, dispatch_id = _deferred_dispatch(repository)
    runtime.mark_agent_dispatch_leased(
        agent_id="worker",
        dispatch_id=dispatch_id,
        now=LEASE_NOW,
    )
    commands = TeamTaskCreationCommandService(repository)

    unchanged, proposal_only = commands.create_task_command(
        session_id="session-worker",
        task_id="unauthorized-root",
        owner_agent_id="worker",
        project_id="project",
        title="Proposed follow-up",
        description="An origin reference must not manufacture root authority.",
        originating_task_id="task",
        request_id="proposal-does-not-create-root",
    )

    assert proposal_only["success"] is False
    assert "create_root_task" in proposal_only["errors"][0]["message"]
    assert "unauthorized-root" not in unchanged.tasks

    admin.update_role(
        role_id="project__worker",
        permissions=[RolePermission.CREATE_ROOT_TASK],
        expected_revision=repository.read().revision,
    )
    unchanged, mismatched_origin = commands.create_task_command(
        session_id="session-worker",
        task_id="cross-project-origin-root",
        owner_agent_id="worker",
        project_id="project",
        title="Invalid cross-project follow-up",
        description="The origin must remain in the target project.",
        originating_task_id="other-origin",
        request_id="cross-project-origin-rejected",
    )

    assert mismatched_origin["success"] is False
    assert (
        "must belong to project project and team team"
        in (mismatched_origin["errors"][0]["message"])
    )
    assert "cross-project-origin-root" not in unchanged.tasks

    state, created = commands.create_task_command(
        session_id="session-worker",
        task_id="authorized-root",
        owner_agent_id="worker",
        project_id="project",
        title="Authorized independent follow-up",
        description="A planner materializes the proposal as an independent root.",
        originating_task_id="task",
        request_id="authorized-origin-root",
    )

    assert created["success"] is True
    follow_up = state.tasks["authorized-root"]
    assert follow_up.parent_task_id is None
    assert follow_up.dependency_task_ids == []
    assert follow_up.originating_task_id == "task"
    assert assess_task(state, "task").readiness.value == "actionable"
    assert assess_task(state, "authorized-root").readiness.value == "actionable"

    state, cancelled = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-worker",
        task_id="task",
        reason_code="superseded_by_follow_up",
        reason="The independent follow-up now owns the remaining scope.",
        successor_task_id="authorized-root",
        request_id="authorized-owner-cancellation",
    )

    assert cancelled["success"] is True
    assert state.tasks["task"].state == TeamTaskState.CANCELLED
    assert state.tasks["task"].cancellation is not None
    assert state.tasks["task"].cancellation.actor_id == "worker"
    assert state.tasks["task"].cancellation.successor_task_id == "authorized-root"
    assert assess_task(state, "authorized-root").readiness.value == "actionable"


def test_parent_owner_cancels_child_from_exact_supervision_handoff(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="child-task",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Child task",
        description="The parent owner must explicitly disposition this child.",
        created_by_agent_id="worker",
        parent_task_id="task",
        expected_revision=repository.read().revision,
    )

    def _add_supervision_handoff(state):
        child = state.tasks["child-task"]
        child.state = TeamTaskState.BLOCKED
        child.state_entered_at = LEASE_NOW
        open_or_get_blocker(
            state,
            source_kind=TeamBlockerSourceKind.TASK,
            source_id=child.id,
            summary="The child cannot complete its obsolete scope.",
            reporter_agent_id="lead",
            team_id="team",
            project_id="project",
            task_id=child.id,
            result={"events": []},
            now=LEASE_NOW,
        )
        state.inbox["child-supervision"] = AgentInboxItem(
            id="child-supervision",
            recipient_agent_id="worker",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            task_id="child-task",
            payload={
                "task_id": "child-task",
                "kind": "child_supervision_required",
            },
        )
        return state

    repository.update(_add_supervision_handoff)
    state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )
    assert len(leases) == 1
    assert leases[0]["reason"] == "inbox"
    assert leases[0]["dispatch"]["task_ids"] == []
    assert "child-supervision" in leases[0]["dispatch"]["inbox_item_ids"]

    state, cancelled = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-worker",
        task_id="child-task",
        reason_code="scope_withdrawn",
        reason="The parent scope no longer requires this child contribution.",
        handled_inbox_item_ids=["child-supervision"],
        request_id="parent-owner-cancels-child",
    )

    assert cancelled["success"] is True
    assert state.tasks["child-task"].state == TeamTaskState.CANCELLED
    assert state.tasks["child-task"].cancellation is not None
    assert state.tasks["child-task"].cancellation.actor_id == "worker"
    assert state.inbox["child-supervision"].state.value == "handled"
    assert assess_task(state, "task").readiness.value == "actionable"
    child_blockers = [
        blocker for blocker in state.blockers.values() if blocker.source_id == "child-task"
    ]
    assert len(child_blockers) == 1
    assert child_blockers[0].state == TeamBlockerState.RESOLVED
    parent_transitions = {
        item.payload.get("transition")
        for item in state.inbox.values()
        if item.payload.get("kind") == "parent_task_transition"
        and item.payload.get("parent_task_id") == "task"
        and "child-task" in item.payload.get("child_task_ids", [])
    }
    assert {"resolution", "integration_ready"} <= parent_transitions
    resolution = next(
        item
        for item in state.inbox.values()
        if item.payload.get("kind") == "parent_task_transition"
        and item.payload.get("transition") == "resolution"
        and "child-task" in item.payload.get("child_task_ids", [])
    )
    assert resolution.state.value == "handled"
    assert resolution.response_requested is False


def test_project_lead_cancels_task_from_role_bounded_operator_chat(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    chat = TeamChatService(repository)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="lead",
        operator_user_id="operator@example.com",
        created_by="operator:operator@example.com",
        expected_revision=repository.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Cancel the superseded worker task.",
        created_by="operator:operator@example.com",
        mutation_authorization=_authorized_chat_mutation(repository, "lead"),
        expected_revision=state.revision,
    )
    state, _message = chat.begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )

    dispatch = state.dispatches[f"team-chat:{message.id}"]
    assert dispatch.task_ids == []
    assert dispatch.project_ids == ["project"]
    assert dispatch.managed_repo_ids == [MANAGED_REPO_ID]

    state, cancelled = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-lead",
        task_id="task",
        reason_code="superseded",
        reason="The operator confirmed that the contribution is already incorporated.",
        request_id="operator-chat-lead-cancellation",
    )

    assert cancelled["success"] is True
    assert state.tasks["task"].state == TeamTaskState.CANCELLED
    assert state.tasks["task"].cancellation is not None
    assert state.tasks["task"].cancellation.actor_id == "lead"


def test_project_lead_task_replacement_rebinds_active_dependents_atomically(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="successor",
        owner_agent_id="worker",
        project_id="project",
        priority=1,
        title="Replacement work",
        description="This task replaces the predecessor.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="active-dependent",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Active downstream work",
        description="This work must follow the replacement.",
        created_by_agent_id="lead",
        dependency_task_ids=["task"],
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="historical-dependent",
        owner_agent_id="lead",
        project_id="project",
        priority=2,
        title="Completed downstream history",
        description="Terminal dependency history must remain immutable.",
        created_by_agent_id="lead",
        dependency_task_ids=["task"],
        expected_revision=repository.read().revision,
    )

    def _finish_successor_and_historical_dependent(state):
        state.tasks["successor"].state = TeamTaskState.FINISHED
        state.tasks["historical-dependent"].state = TeamTaskState.FINISHED
        return state

    repository.update(_finish_successor_and_historical_dependent)
    _begin_project_lead_chat_dispatch(
        repository,
        body="Replace the predecessor and preserve its downstream graph.",
    )

    unchanged, stale_plan = TeamTaskUpdateCommandService(
        repository
    ).cancel_task_command(
        session_id="session-lead",
        task_id="task",
        reason_code="replaced",
        reason="Do not widen a stale replacement plan.",
        successor_task_id="successor",
        rebind_dependents=True,
        expected_dependent_task_ids=[],
        request_id="reject-expanded-task-replacement",
    )

    assert stale_plan["success"] is False
    assert stale_plan["errors"][0]["code"] == (
        "task_replacement_affected_set_mismatch"
    )
    assert stale_plan["errors"][0]["correction_context"][
        "current_dependent_task_ids"
    ] == ["active-dependent"]
    assert unchanged.tasks["task"].state == TeamTaskState.PENDING
    assert unchanged.tasks["task"].cancellation is None
    assert unchanged.tasks["active-dependent"].dependency_task_ids == ["task"]

    state, replaced = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-lead",
        task_id="task",
        reason_code="replaced",
        reason="The successor now owns the remaining work.",
        successor_task_id="successor",
        rebind_dependents=True,
        expected_dependent_task_ids=["active-dependent"],
        request_id="replace-task-and-rebind-dependents",
    )

    assert replaced["success"] is True
    assert replaced["rebind_dependents"] is True
    assert replaced["rebound_dependent_task_ids"] == ["active-dependent"]
    assert replaced["newly_actionable_dependent_task_ids"] == ["active-dependent"]
    assert replaced["dependency_rebindings"] == [
        {
            "dependent_task_id": "active-dependent",
            "predecessor_task_id": "task",
            "successor_task_id": "successor",
            "dependency_task_ids": ["successor"],
        }
    ]
    generation_change = replaced["preparation_generation_changes"]["active-dependent"]
    assert generation_change["before"] != generation_change["after"]
    assert state.tasks["active-dependent"].dependency_task_ids == ["successor"]
    assert state.tasks["historical-dependent"].dependency_task_ids == ["task"]
    cancellation = state.tasks["task"].cancellation
    assert cancellation is not None
    assert cancellation.rebind_dependents is True
    assert cancellation.rebound_dependent_task_ids == ["active-dependent"]
    task_ready_events = [
        event
        for event in replaced.get("events", [])
        if event["payload"].get("reason") == "task_ready"
    ]
    assert task_ready_events == []


def test_task_owner_can_cancel_but_cannot_rewrite_dependents(tmp_path: Path) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="successor",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Replacement work",
        description="A lead may select this for an audited graph replacement.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="dependent",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Dependent work",
        description="An ordinary predecessor owner cannot rewrite this edge.",
        created_by_agent_id="lead",
        dependency_task_ids=["task"],
        expected_revision=repository.read().revision,
    )
    _state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )
    assert len(leases) == 1

    state, forbidden = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-worker",
        task_id="task",
        reason_code="replaced",
        reason="An ordinary owner must not rewrite another task's dependency graph.",
        successor_task_id="successor",
        rebind_dependents=True,
        expected_dependent_task_ids=["dependent"],
        request_id="ordinary-owner-cannot-rebind-dependents",
    )

    assert forbidden["success"] is False
    assert forbidden["errors"][0]["code"] == "task_replacement_not_permitted"
    assert state.tasks["task"].state == TeamTaskState.PENDING
    assert state.tasks["dependent"].dependency_task_ids == ["task"]

    state, cancelled = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-worker",
        task_id="task",
        reason_code="obsolete",
        reason="The owner may still apply a plain audited cancellation.",
        successor_task_id="successor",
        request_id="ordinary-owner-plain-cancellation",
    )

    assert cancelled["success"] is True
    assert state.tasks["task"].state == TeamTaskState.CANCELLED
    assert state.tasks["task"].cancellation is not None
    assert state.tasks["task"].cancellation.rebind_dependents is False
    assert state.tasks["task"].cancellation.successor_task_id == "successor"
    assert state.tasks["dependent"].dependency_task_ids == ["task"]


def test_project_lead_repairs_plain_cancellation_idempotently_with_frozen_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_plain_cancelled_task_with_stranded_dependent(repository)
    original_cancellation = repository.read().tasks["task"].cancellation
    assert original_cancellation is not None
    original_cancellation_payload = original_cancellation.model_dump(mode="json")
    _begin_project_lead_chat_dispatch(
        repository,
        body="Repair only the frozen downstream set from the recorded successor.",
    )
    commands = TeamTaskUpdateCommandService(repository)

    unchanged, stale_plan = commands.repair_cancelled_task_dependencies_command(
        session_id="session-lead",
        predecessor_task_id="task",
        successor_task_id="successor",
        expected_dependent_task_ids=[],
        reason="The caller must not widen an out-of-date repair plan.",
        request_id="reject-expanded-cancelled-task-repair",
    )

    assert stale_plan["success"] is False
    assert stale_plan["errors"][0]["code"] == (
        "task_dependency_repair_affected_set_mismatch"
    )
    assert stale_plan["errors"][0]["correction_context"][
        "current_dependent_task_ids"
    ] == ["dependent"]
    assert unchanged.tasks["dependent"].dependency_task_ids == ["task"]

    state, repaired = commands.repair_cancelled_task_dependencies_command(
        session_id="session-lead",
        predecessor_task_id="task",
        successor_task_id="successor",
        expected_dependent_task_ids=["dependent"],
        reason="Repair the stranded edge without changing cancellation history.",
        request_id="repair-cancelled-task-dependencies",
    )

    assert repaired["success"] is True
    assert repaired["repair_applied"] is True
    assert repaired["rebound_dependent_task_ids"] == ["dependent"]
    assert repaired["newly_actionable_dependent_task_ids"] == []
    assert state.tasks["dependent"].dependency_task_ids == ["successor"]
    assert state.tasks["task"].cancellation.model_dump(mode="json") == (
        original_cancellation_payload
    )
    audit_evidence_id = repaired["audit_action_evidence_id"]
    evidence = state.inbox[audit_evidence_id]
    assert evidence.item_type == TeamInboxItemType.PROGRESS_REPORT
    assert evidence.state == TeamInboxItemState.HANDLED
    assert evidence.recipient_agent_id == "lead"
    assert evidence.payload["kind"] == "task_dependency_graph_repair_audit"
    assert evidence.payload["actor_kind"] == "agent"
    assert evidence.payload["actor_id"] == "lead"
    assert evidence.payload["expected_dependent_task_ids"] == ["dependent"]

    revision_before_repeat = state.revision
    runtime_before_repeat = state.agent_runtimes["lead"].model_dump(mode="json")
    outbox_count_before_repeat = len(repository.list_outbox_records())
    state, repeated = commands.repair_cancelled_task_dependencies_command(
        session_id="session-lead",
        predecessor_task_id="task",
        successor_task_id="successor",
        expected_dependent_task_ids=["dependent"],
        reason="Verify the previously recorded repair is still complete.",
        request_id="repeat-cancelled-task-dependency-repair",
    )

    assert repeated["success"] is True
    assert repeated["repair_applied"] is False
    assert repeated["rebound_dependent_task_ids"] == []
    assert repeated["audit_evidence_reused"] is True
    assert repeated["audit_action_evidence_id"] == audit_evidence_id
    assert state.revision == revision_before_repeat
    assert state.agent_runtimes["lead"].model_dump(mode="json") == (
        runtime_before_repeat
    )
    assert len(repository.list_outbox_records()) == outbox_count_before_repeat
    assert sum(
        item.payload.get("kind") == "task_dependency_graph_repair_audit"
        for item in state.inbox.values()
    ) == 1
    assert state.tasks["task"].cancellation.model_dump(mode="json") == (
        original_cancellation_payload
    )


def test_ordinary_task_owner_cannot_repair_plain_cancellation_graph(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="successor",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Replacement task",
        description="The recorded replacement.",
        created_by_agent_id="lead",
        state=TeamTaskState.FINISHED,
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="dependent",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Stranded downstream task",
        description="Only a lead or manager may rewrite this dependency.",
        created_by_agent_id="lead",
        dependency_task_ids=["task"],
        expected_revision=repository.read().revision,
    )
    _state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )
    assert len(leases) == 1
    assert leases[0]["dispatch"]["task_ids"] == ["task"]
    admin.cancel_task(
        task_id="task",
        reason_code="replaced",
        reason="Record the successor without rewriting dependents.",
        actor_id="operator:test",
        successor_task_id="successor",
        expected_revision=repository.read().revision,
    )

    state, forbidden = TeamTaskUpdateCommandService(
        repository
    ).repair_cancelled_task_dependencies_command(
        session_id="session-worker",
        predecessor_task_id="task",
        successor_task_id="successor",
        expected_dependent_task_ids=["dependent"],
        reason="An ordinary owner must not rewrite the project graph.",
        request_id="ordinary-owner-cannot-repair-cancelled-task",
    )

    assert forbidden["success"] is False
    assert forbidden["errors"][0]["code"] == "task_dependency_repair_not_permitted"
    assert state.tasks["dependent"].dependency_task_ids == ["task"]
    assert all(
        item.payload.get("kind") != "task_dependency_graph_repair_audit"
        for item in state.inbox.values()
    )


def test_team_manager_can_repair_plain_cancellation_graph(tmp_path: Path) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_plain_cancelled_task_with_stranded_dependent(repository)
    _begin_project_chat_dispatch(
        repository,
        agent_id="manager",
        body="Repair the project's frozen cancelled-task dependency set.",
    )

    state, repaired = TeamTaskUpdateCommandService(
        repository
    ).repair_cancelled_task_dependencies_command(
        session_id="session-manager",
        predecessor_task_id="task",
        successor_task_id="successor",
        expected_dependent_task_ids=["dependent"],
        reason="The Team manager authorizes this bounded graph repair.",
        request_id="team-manager-repairs-cancelled-task",
    )

    assert repaired["success"] is True
    assert state.tasks["dependent"].dependency_task_ids == ["successor"]
    evidence = state.inbox[repaired["audit_action_evidence_id"]]
    assert evidence.payload["actor_id"] == "manager"


def test_cancelled_task_repair_requires_target_project_in_dispatch_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_plain_cancelled_task_with_stranded_dependent(repository)
    _begin_project_lead_chat_dispatch(
        repository,
        body="This dispatch belongs only to the unrelated project.",
    )
    _seed_secondary_project_for_lead(repository)
    _replace_active_dispatch_project_scope(
        repository,
        agent_id="lead",
        project_ids=["other-project"],
    )

    state, rejected = TeamTaskUpdateCommandService(
        repository
    ).repair_cancelled_task_dependencies_command(
        session_id="session-lead",
        predecessor_task_id="task",
        successor_task_id="successor",
        expected_dependent_task_ids=["dependent"],
        reason="Do not authorize primary-project repair from another project scope.",
        request_id="repair-outside-dispatch-project",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.tasks["dependent"].dependency_task_ids == ["task"]


def test_cross_team_manager_cannot_repair_cancelled_task_dependencies(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_plain_cancelled_task_with_stranded_dependent(repository)
    _seed_forged_cross_team_manager_dispatch(repository)

    state, rejected = TeamTaskUpdateCommandService(
        repository
    ).repair_cancelled_task_dependencies_command(
        session_id="session-foreign-manager",
        predecessor_task_id="task",
        successor_task_id="successor",
        expected_dependent_task_ids=["dependent"],
        reason="A foreign manager must not govern another Team's project graph.",
        request_id="foreign-manager-cannot-repair-cancelled-task",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == (
        "task_dependency_repair_not_permitted"
    )
    assert state.tasks["dependent"].dependency_task_ids == ["task"]


@pytest.mark.parametrize(
    ("cause_kind", "source_kind", "source_id", "expected_error_code"),
    [
        (
            "scheduled",
            TeamBlockerSourceKind.TASK,
            "task",
            "dispatch_scope_forbidden",
        ),
        (
            "scheduled",
            TeamBlockerSourceKind.DELIVERABLE,
            "cross-team-deliverable",
            "dispatch_scope_forbidden",
        ),
        (
            "team_chat",
            TeamBlockerSourceKind.TASK,
            "task",
            "dispatch_scope_forbidden",
        ),
        (
            "team_chat",
            TeamBlockerSourceKind.DELIVERABLE,
            "cross-team-deliverable",
            "dispatch_scope_forbidden",
        ),
        (
            "team_chat",
            TeamBlockerSourceKind.RELEASE_PACKAGE,
            "cross-team-release",
            "blocker_source_not_permitted",
        ),
    ],
)
def test_cross_team_manager_cannot_report_project_scoped_diagnostic_blocker(
    tmp_path: Path,
    cause_kind: str,
    source_kind: TeamBlockerSourceKind,
    source_id: str,
    expected_error_code: str,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    if source_kind == TeamBlockerSourceKind.DELIVERABLE:

        def _seed_deliverable(state):
            state.deliverables[source_id] = Deliverable(
                id=source_id,
                task_id="task",
                producer_agent_id="worker",
                summary="A handoff owned by another Team's project.",
            )
            state.tasks["task"].deliverable_ids = [source_id]
            return state

        repository.update(_seed_deliverable)
    elif source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE:

        def _seed_release(state):
            state.release_packages[source_id] = ReleasePackage(
                id=source_id,
                project_id="project",
                release_branch="team/team/releases/cross-team-release",
                candidate_commit_sha="a" * 40,
                candidate_tree_hash="b" * 40,
            )
            return state

        repository.update(_seed_release)
    _seed_forged_cross_team_manager_dispatch(
        repository,
        cause_kind=cause_kind,
    )

    state, rejected = TeamBlockerCommandService(repository).report_blocker_command(
        session_id="session-foreign-manager",
        source_kind=source_kind,
        source_id=source_id,
        summary="A foreign manager must not gain project diagnostic authority.",
        request_id=(
            f"foreign-manager-cannot-report-{cause_kind}-{source_kind.value}-blocker"
        ),
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == expected_error_code
    assert state.blockers == {}
    assert state.tasks["task"].state == TeamTaskState.PENDING
    if source_kind == TeamBlockerSourceKind.DELIVERABLE:
        assert state.deliverables[source_id].state == TeamRecordState.PENDING


def test_forged_cross_team_manager_chat_cannot_edit_task_graph(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    TeamAdminService(repository).create_or_reprioritize_task(
        task_id="dependency",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Dependency",
        description="This edge belongs to the target project's Team.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    _seed_forged_cross_team_manager_dispatch(
        repository,
        cause_kind="team_chat",
    )

    state, rejected = TeamTaskUpdateCommandService(repository).update_task_command(
        session_id="session-foreign-manager",
        task_id="task",
        dependency_task_ids=["dependency"],
        request_id="foreign-manager-chat-cannot-edit-task-graph",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "task_graph_not_permitted"
    assert state.tasks["task"].dependency_task_ids == []


def test_legacy_action_forged_cross_team_manager_chat_cannot_edit_task_graph(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    TeamAdminService(repository).create_or_reprioritize_task(
        task_id="legacy-dependency",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Legacy dependency",
        description="This edge belongs to the target project's Team.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    _seed_forged_cross_team_manager_dispatch(
        repository,
        cause_kind="team_chat",
    )
    dispatch_id = repository.read().agent_runtimes[
        "foreign-manager"
    ].active_dispatch_id
    assert dispatch_id is not None

    state, rejected = TeamAgentActionService(repository).apply_agent_action(
        session_id="session-foreign-manager",
        dispatch_id=dispatch_id,
        action={
            "agent_id": "foreign-manager",
            "task_updates": [
                {
                    "task_id": "task",
                    "dependency_task_ids": ["legacy-dependency"],
                }
            ],
        },
    )

    assert rejected["success"] is False
    assert any("not permitted to edit the task graph" in error for error in rejected["errors"])
    assert state.tasks["task"].dependency_task_ids == []


def test_legacy_action_operator_escalation_rejects_cross_team_task_lineage(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_forged_cross_team_manager_dispatch(
        repository,
        cause_kind="team_chat",
    )
    admin = TeamAdminService(repository)
    for suffix in ("a", "b"):
        admin.create_project(
            project_id=f"foreign-project-{suffix}",
            team_id="foreign-team",
            name=f"Foreign Project {suffix.upper()}",
            lead_agent_id=f"foreign-lead-{suffix}",
            lead_session_id=f"session-foreign-lead-{suffix}",
            goals=[ProjectGoal(id=f"foreign-goal-{suffix}", description="Ship work")],
            expected_revision=repository.read().revision,
        )
    admin.create_or_reprioritize_task(
        task_id="foreign-task",
        owner_agent_id="foreign-lead-a",
        project_id="foreign-project-a",
        priority=1,
        title="Foreign Team task",
        description="This task belongs to foreign project A.",
        created_by_agent_id="foreign-lead-a",
        expected_revision=repository.read().revision,
    )

    def _configure_operator_target(state):
        state.teams["foreign-team"].operator_communication_target_id = (
            "foreign-team-operators"
        )
        return state

    repository.update(_configure_operator_target)
    dispatch_id = repository.read().agent_runtimes[
        "foreign-manager"
    ].active_dispatch_id
    assert dispatch_id is not None

    state, rejected = TeamAgentActionService(repository).apply_agent_action(
        session_id="session-foreign-manager",
        dispatch_id=dispatch_id,
        action={
            "agent_id": "foreign-manager",
            "operator_escalations": [
                {
                    "title": "Foreign task escalation",
                    "message": "This task must remain within its owning Team.",
                    "task_id": "task",
                },
                {
                    "title": "Missing task escalation",
                    "message": "A missing task cannot provide escalation lineage.",
                    "task_id": "missing-task",
                },
                {
                    "title": "Mismatched project escalation",
                    "message": "The supplied task and project must agree.",
                    "project_id": "foreign-project-b",
                    "task_id": "foreign-task",
                },
            ],
        },
    )

    assert rejected["success"] is False
    assert any("outside team foreign-team" in error for error in rejected["errors"])
    assert any("missing-task does not exist" in error for error in rejected["errors"])
    assert any(
        "belongs to project foreign-project-a, not project foreign-project-b" in error
        for error in rejected["errors"]
    )
    assert rejected["operator_escalation_event_ids"] == []
    assert state.inbox == {}


def test_forged_cross_team_manager_chat_cannot_request_deliverable_review(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)

    def _seed_deliverable(state):
        state.deliverables["cross-team-review-deliverable"] = Deliverable(
            id="cross-team-review-deliverable",
            task_id="task",
            producer_agent_id="worker",
            summary="A deliverable governed by the target project's Team.",
        )
        state.tasks["task"].deliverable_ids = ["cross-team-review-deliverable"]
        return state

    repository.update(_seed_deliverable)
    _seed_forged_cross_team_manager_dispatch(
        repository,
        cause_kind="team_chat",
    )

    state, rejected = TeamReviewCommandService(repository).request_review_command(
        session_id="session-foreign-manager",
        deliverable_id="cross-team-review-deliverable",
        reviewer_agent_id="foreign-manager",
        request_id="foreign-manager-chat-cannot-request-review",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "review_request_failed"
    assert "not permitted" in rejected["errors"][0]["message"]
    assert state.review_requests == {}
    assert state.deliverables["cross-team-review-deliverable"].review_request_ids == []


def test_forged_cross_team_manager_chat_cannot_govern_project_cases(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_cross_team_case_command_targets(repository)
    _seed_forged_cross_team_manager_dispatch(
        repository,
        cause_kind="team_chat",
    )
    service = TeamCaseCommandService(repository)

    state, proposal = service.propose_human_finding_resolution_command(
        session_id="session-foreign-manager",
        finding_id="human-finding",
        proposed_resolution="A foreign Team must not set this resolution.",
        evidence_refs=["test:foreign-manager"],
        proposed_by_deliverable_ids=[],
        request_id="foreign-manager-chat-cannot-propose-human-resolution",
    )
    assert proposal["success"] is False
    assert proposal["errors"][0]["code"] == (
        "human_finding_resolution_not_permitted"
    )

    state, routing = service.route_human_finding_correction_command(
        session_id="session-foreign-manager",
        finding_id="human-finding",
        owner_agent_id="worker",
        request_id="foreign-manager-chat-cannot-route-human-correction",
    )
    assert routing["success"] is False
    assert routing["errors"][0]["code"] == "human_finding_route_not_permitted"

    state, closure = service.close_post_release_feedback_case_command(
        session_id="session-foreign-manager",
        case_id="post-release-case",
        reason="A foreign Team must not close this case.",
        evidence_refs=["test:foreign-manager"],
        request_id="foreign-manager-chat-cannot-close-post-release-case",
    )
    assert closure["success"] is False
    assert closure["errors"][0]["code"] == (
        "post_release_case_close_not_permitted"
    )

    assert state.human_review_findings["human-finding"].proposed_resolution is None
    assert state.tasks["human-correction"].owner_agent_id == "lead"
    assert state.cases["post-release-case"].status == TeamCaseStatus.IN_PROGRESS


def test_cancelled_task_repair_requires_recorded_successor(tmp_path: Path) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_plain_cancelled_task_with_stranded_dependent(repository)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="other-successor",
        owner_agent_id="worker",
        project_id="project",
        priority=3,
        title="Unrecorded alternative",
        description="This task is not the cancellation's recorded successor.",
        created_by_agent_id="lead",
        state=TeamTaskState.FINISHED,
        expected_revision=repository.read().revision,
    )
    original_cancellation = repository.read().tasks["task"].cancellation.model_dump(
        mode="json"
    )
    _begin_project_lead_chat_dispatch(
        repository,
        body="Do not substitute a successor absent from the cancellation record.",
    )

    state, rejected = TeamTaskUpdateCommandService(
        repository
    ).repair_cancelled_task_dependencies_command(
        session_id="session-lead",
        predecessor_task_id="task",
        successor_task_id="other-successor",
        expected_dependent_task_ids=["dependent"],
        reason="Attempt an invalid successor substitution.",
        request_id="reject-unrecorded-repair-successor",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == (
        "task_dependency_repair_successor_mismatch"
    )
    assert state.tasks["dependent"].dependency_task_ids == ["task"]
    assert state.tasks["task"].cancellation.model_dump(mode="json") == (
        original_cancellation
    )


@pytest.mark.parametrize(
    "dependency_state",
    [TeamTaskState.CANCELLED, TeamTaskState.REJECTED],
)
def test_agent_task_creation_rejects_dead_end_dependency(
    tmp_path: Path,
    dependency_state: TeamTaskState,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    if dependency_state == TeamTaskState.CANCELLED:
        _seed_plain_cancelled_task_with_stranded_dependent(repository)
    else:
        def _reject_dependency(state):
            state.tasks["task"].state = TeamTaskState.REJECTED
            return state

        repository.update(_reject_dependency)
    _begin_project_lead_chat_dispatch(
        repository,
        body="Create follow-up work only against live dependency frontiers.",
    )

    state, rejected = TeamTaskCreationCommandService(repository).create_task_command(
        session_id="session-lead",
        task_id="new-stale-dependent",
        owner_agent_id="lead",
        project_id="project",
        title="Invalid newly stranded task",
        description="A new task cannot depend on an existing dead-end predecessor.",
        dependency_task_ids=["task"],
        request_id=f"reject-new-{dependency_state.value}-dependency",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "task_graph_invalid"
    assert (
        f"because it is {dependency_state.value}"
        in rejected["errors"][0]["message"]
    )
    assert "new-stale-dependent" not in state.tasks


@pytest.mark.parametrize(
    ("locked_field", "integration_boundary"),
    [
        ("dependency_task_ids", False),
        ("deliverable_contract", True),
    ],
)
def test_task_replacement_respects_dependent_mutation_policy(
    tmp_path: Path,
    locked_field: str,
    integration_boundary: bool,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="successor",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Replacement work",
        description="This successor must not override workflow topology policy.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="managed-dependent",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Workflow-managed dependent",
        description="Its workflow-owned topology is immutable.",
        created_by_agent_id="lead",
        dependency_task_ids=["task"],
        integration_boundary=integration_boundary,
        deliverable_contract=(
            {"required_contribution_task_ids": ["task"]}
            if integration_boundary
            else None
        ),
        expected_revision=repository.read().revision,
    )

    def _lock_dependent(state):
        state.tasks["managed-dependent"].mutation_policy = TaskMutationPolicy(
            policy_id="workflow-owned-topology-v1",
            reason="The workflow owns this task topology.",
            locked_fields=[locked_field],
        )
        return state

    repository.update(_lock_dependent)
    _begin_project_lead_chat_dispatch(
        repository,
        body="Replace only if workflow-owned dependent topology permits it.",
    )

    state, rejected = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-lead",
        task_id="task",
        reason_code="replaced",
        reason="The mutation policy must remain authoritative.",
        successor_task_id="successor",
        rebind_dependents=True,
        expected_dependent_task_ids=["managed-dependent"],
        request_id=f"replacement-respects-{locked_field}-lock",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "task_replacement_graph_invalid"
    assert locked_field in rejected["errors"][0]["message"]
    assert state.tasks["task"].state == TeamTaskState.PENDING
    assert state.tasks["task"].cancellation is None
    assert state.tasks["managed-dependent"].dependency_task_ids == ["task"]
    if integration_boundary:
        assert state.tasks[
            "managed-dependent"
        ].deliverable_contract.required_contribution_task_ids == ["task"]


def test_task_replacement_rearms_stale_integration_preparation_without_rewriting_provenance(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="successor",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Replacement contribution",
        description="This contribution replaces the predecessor.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="integration",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Integrate contributions",
        description="Prepare the retained contribution frontier.",
        created_by_agent_id="lead",
        dependency_task_ids=["task"],
        integration_boundary=True,
        deliverable_contract={
            "require_git_provenance": True,
            "required_contribution_task_ids": ["task"],
        },
        expected_revision=repository.read().revision,
    )
    original_source = DependencyGitSource(
        dependency_task_id="task",
        deliverable_id="predecessor-deliverable",
        branch="agents/session-worker/tasks/task",
        commit_sha="a" * 40,
        relationship_kind="required_contribution",
        pinned_ref="refs/evander/team-reviews/review-predecessor",
    )
    original_manifest = DependencyCompositionManifest(
        target_task_id="integration",
        managed_repo_id=MANAGED_REPO_ID,
        directory_path="root",
        base_branch="main",
        base_commit_sha="b" * 40,
        base_tree_hash="c" * 40,
        dependency_git_sources=[original_source],
        source_fingerprint="source-fingerprint",
        frontier_commit_shas=["a" * 40],
        composed_commit_sha="d" * 40,
        composed_tree_hash="e" * 40,
    )

    def _seed_prepared_integration(state):
        # Model both contributions as direct children rather than explicit
        # dependencies; replacement must move the contract frontier even though
        # there is no dependency-list edge to rewrite.
        state.tasks["integration"].dependency_task_ids = []
        state.tasks["task"].parent_task_id = "integration"
        state.tasks["successor"].parent_task_id = "integration"
        state.git_work_contexts["integration-context"] = GitWorkContext(
            id="integration-context",
            agent_id="lead",
            task_id="integration",
            agent_branch="agents/session-lead/tasks/integration",
            base_branch="main",
            base_commit="d" * 40,
            worktree_path="/worktrees/integration-context",
            dependency_git_sources=[original_source],
            dependency_composition_manifest=original_manifest,
        )
        return state

    repository.update(_seed_prepared_integration)
    prepared = repository.read().git_work_contexts["integration-context"]
    immutable_sources = [
        source.model_dump(mode="json") for source in prepared.dependency_git_sources
    ]
    immutable_manifest = prepared.dependency_composition_manifest.model_dump(mode="json")
    _begin_project_lead_chat_dispatch(
        repository,
        body="Replace the predecessor contribution and re-arm integration.",
    )

    state, replaced = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-lead",
        task_id="task",
        reason_code="replaced",
        reason="The successor owns this contribution frontier.",
        successor_task_id="successor",
        rebind_dependents=True,
        expected_dependent_task_ids=["integration"],
        request_id="replace-stale-integration-preparation",
    )

    assert replaced["success"] is True
    assert replaced["rebound_dependent_task_ids"] == ["integration"]
    assert replaced["affected_integration_boundary_task_ids"] == ["integration"]
    assert replaced["invalidated_git_work_context_ids"] == ["integration-context"]
    assert replaced["dependency_rebindings"] == []
    assert replaced["required_contribution_rebindings"] == [
        {
            "integration_task_id": "integration",
            "predecessor_task_id": "task",
            "successor_task_id": "successor",
            "required_contribution_task_ids": ["successor"],
        }
    ]
    integration = state.tasks["integration"]
    assert integration.dependency_task_ids == []
    assert integration.deliverable_contract == TaskDeliverableContract(
        require_git_provenance=True,
        required_contribution_task_ids=["successor"],
    )
    cancellation = state.tasks["task"].cancellation
    assert cancellation is not None
    assert cancellation.rebound_integration_boundary_task_ids == ["integration"]
    assert cancellation.invalidated_git_work_context_ids == ["integration-context"]
    invalidated = state.git_work_contexts["integration-context"]
    assert invalidated.state == GitWorkContextState.ABANDONED
    assert [source.model_dump(mode="json") for source in invalidated.dependency_git_sources] == (
        immutable_sources
    )
    assert invalidated.dependency_composition_manifest is not None
    assert invalidated.dependency_composition_manifest.model_dump(mode="json") == immutable_manifest
    [recovery_inbox_id] = replaced["created_preparation_recovery_inbox_item_ids"]
    recovery = state.inbox[recovery_inbox_id]
    assert recovery.recipient_agent_id == "lead"
    assert recovery.payload["kind"] == "task_replacement_preparation_recovery"
    assert recovery.payload["invalidated_git_work_context_ids"] == ["integration-context"]
    assert recovery.payload["required_action"] == ("reprepare_from_current_contribution_frontier")
    assert (
        recovery.payload["preparation_generation_before"]
        != recovery.payload["preparation_generation_after"]
    )


def test_task_replacement_does_not_rewrite_submitted_integration_frontier(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="successor",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Replacement contribution",
        description="This contribution arrived after integration submission.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="integration",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Submitted integration",
        description="Its submitted frontier is immutable.",
        created_by_agent_id="lead",
        dependency_task_ids=["task"],
        integration_boundary=True,
        deliverable_contract={"required_contribution_task_ids": ["task"]},
        expected_revision=repository.read().revision,
    )

    def _seed_submission(state):
        state.deliverables["integration-deliverable"] = Deliverable(
            id="integration-deliverable",
            task_id="integration",
            producer_agent_id="lead",
            summary="Submitted from the predecessor frontier.",
        )
        state.tasks["integration"].deliverable_ids = ["integration-deliverable"]
        state.tasks["integration"].state = TeamTaskState.WAITING_FOR_REVIEW
        state.git_work_contexts["integration-context"] = GitWorkContext(
            id="integration-context",
            agent_id="lead",
            task_id="integration",
            agent_branch="agents/session-lead/tasks/integration",
            base_branch="main",
            base_commit="a" * 40,
        )
        return state

    repository.update(_seed_submission)
    _begin_project_lead_chat_dispatch(
        repository,
        body="Attempt the replacement without rewriting submitted provenance.",
    )

    state, rejected = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-lead",
        task_id="task",
        reason_code="replaced",
        reason="A submitted integration must not have its frontier rewritten.",
        successor_task_id="successor",
        rebind_dependents=True,
        expected_dependent_task_ids=["integration"],
        request_id="reject-submitted-integration-frontier-rewrite",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "task_replacement_graph_invalid"
    assert "after it submitted a deliverable" in rejected["errors"][0]["message"]
    assert state.tasks["task"].state == TeamTaskState.PENDING
    assert state.tasks["task"].cancellation is None
    assert state.tasks["integration"].dependency_task_ids == ["task"]
    assert state.tasks[
        "integration"
    ].deliverable_contract.required_contribution_task_ids == ["task"]
    assert state.git_work_contexts["integration-context"].state == GitWorkContextState.ACTIVE


def _seed_submitted_integration_boundary_recovery(
    repository: TeamStateRepository,
    *,
    require_git_provenance: bool = True,
) -> None:
    admin = TeamAdminService(repository)
    if not require_git_provenance:

        def _remove_project_git(state):
            state.projects["project"].git = None
            return state

        repository.update(_remove_project_git)
    admin.create_or_reprioritize_task(
        task_id="successor",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Accepted successor contribution",
        description="This task is the cancelled contribution's recorded successor.",
        created_by_agent_id="lead",
        state=TeamTaskState.FINISHED,
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="integration",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Submitted integration boundary",
        description="Rebuild this boundary without rewriting its submitted history.",
        created_by_agent_id="lead",
        dependency_task_ids=["task"],
        integration_boundary=True,
        deliverable_contract={
            "require_git_provenance": require_git_provenance,
            "required_contribution_task_ids": ["task"],
        },
        expected_revision=repository.read().revision,
    )

    def _submit_integration(state):
        review_snapshot = (
            ReviewRepositorySnapshot(
                managed_repo_id=MANAGED_REPO_ID,
                directory_path="root",
                commit_sha="d" * 40,
                pinned_ref="refs/evander/team-reviews/integration-review",
                source_kind="deliverable_candidate",
                source_ref="agents/session-lead/tasks/integration",
            )
            if require_git_provenance
            else None
        )
        state.deliverables["integration-deliverable"] = Deliverable(
            id="integration-deliverable",
            task_id="integration",
            producer_agent_id="lead",
            summary="Submitted from the original contribution frontier.",
            review_request_ids=["integration-review"],
        )
        state.review_requests["integration-review"] = ReviewRequest(
            id="integration-review",
            deliverable_id="integration-deliverable",
            reviewer_agent_id="worker",
            requested_by_agent_id="lead",
            repository_snapshot=review_snapshot,
        )
        integration = state.tasks["integration"]
        integration.deliverable_ids = ["integration-deliverable"]
        integration.state = TeamTaskState.WAITING_FOR_REVIEW
        if require_git_provenance:
            state.git_work_contexts["integration-context"] = GitWorkContext(
                id="integration-context",
                agent_id="lead",
                task_id="integration",
                agent_branch="agents/session-lead/tasks/integration",
                base_branch="main",
                base_commit="b" * 40,
                commit_sha="d" * 40,
                state=GitWorkContextState.SUBMITTED,
            )
        return state

    repository.update(_submit_integration)
    admin.cancel_task(
        task_id="task",
        reason_code="replaced",
        reason="The successor owns this contribution going forward.",
        actor_id="operator:test",
        successor_task_id="successor",
        expected_revision=repository.read().revision,
    )


def _recover_submitted_integration_as_lead(
    repository: TeamStateRepository,
    *,
    replacement_task_id: str = "integration-recovery",
    expected_dependency_task_ids: list[str] | None = None,
    expected_required_contribution_task_ids: list[str] | None = None,
    git_base_commit_sha: str | None = "f" * 40,
    git_base_branch: str | None = "main",
    reason: str = (
        "Rebuild the submitted boundary from its frozen successor frontier."
    ),
    request_id: str = "recover-submitted-integration",
) -> tuple[object, dict[str, object]]:
    return TeamTaskUpdateCommandService(
        repository
    ).recover_submitted_integration_boundary_command(
        session_id="session-lead",
        predecessor_task_id="task",
        successor_task_id="successor",
        integration_task_id="integration",
        replacement_task_id=replacement_task_id,
        expected_dependency_task_ids=(
            ["task"]
            if expected_dependency_task_ids is None
            else expected_dependency_task_ids
        ),
        expected_required_contribution_task_ids=(
            ["task"]
            if expected_required_contribution_task_ids is None
            else expected_required_contribution_task_ids
        ),
        reason=reason,
        git_base_commit_sha=git_base_commit_sha,
        git_base_branch=git_base_branch,
        request_id=request_id,
    )


def test_ordinary_agent_cannot_recover_submitted_integration_boundary(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_submitted_integration_boundary_recovery(repository)
    _begin_project_chat_dispatch(
        repository,
        agent_id="worker",
        body="Attempt the project-governed submitted-boundary recovery.",
    )

    state, rejected = TeamTaskUpdateCommandService(
        repository
    ).recover_submitted_integration_boundary_command(
        session_id="session-worker",
        predecessor_task_id="task",
        successor_task_id="successor",
        integration_task_id="integration",
        replacement_task_id="integration-recovery",
        expected_dependency_task_ids=["task"],
        expected_required_contribution_task_ids=["task"],
        reason="An ordinary agent must not replace a submitted integration boundary.",
        git_base_commit_sha="f" * 40,
        git_base_branch="main",
        request_id="ordinary-agent-submitted-integration-recovery",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == (
        "submitted_integration_recovery_not_permitted"
    )
    assert "integration-recovery" not in state.tasks


def test_submitted_integration_recovery_requires_target_project_in_dispatch_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_submitted_integration_boundary_recovery(repository)
    _begin_project_lead_chat_dispatch(
        repository,
        body="This dispatch belongs only to the unrelated project.",
    )
    _seed_secondary_project_for_lead(repository)
    _replace_active_dispatch_project_scope(
        repository,
        agent_id="lead",
        project_ids=["other-project"],
    )

    state, rejected = _recover_submitted_integration_as_lead(
        repository,
        request_id="recover-boundary-outside-dispatch-project",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert "integration-recovery" not in state.tasks


def test_cross_team_manager_cannot_recover_submitted_integration_boundary(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_submitted_integration_boundary_recovery(repository)
    _seed_forged_cross_team_manager_dispatch(repository)

    state, rejected = TeamTaskUpdateCommandService(
        repository
    ).recover_submitted_integration_boundary_command(
        session_id="session-foreign-manager",
        predecessor_task_id="task",
        successor_task_id="successor",
        integration_task_id="integration",
        replacement_task_id="integration-recovery",
        expected_dependency_task_ids=["task"],
        expected_required_contribution_task_ids=["task"],
        reason="A foreign manager must not govern another Team's recovery boundary.",
        git_base_commit_sha="f" * 40,
        git_base_branch="main",
        request_id="foreign-manager-cannot-recover-integration",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == (
        "submitted_integration_recovery_not_permitted"
    )
    assert "integration-recovery" not in state.tasks


def test_submitted_integration_recovery_rejects_late_high_priority_instruction(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_submitted_integration_boundary_recovery(repository)
    _begin_project_lead_chat_dispatch(
        repository,
        body="Recover the submitted boundary from this frozen dispatch snapshot.",
    )
    wake_at = datetime(2098, 12, 31, 23, 58, tzinfo=UTC)

    def _add_late_instruction(state):
        state.agent_runtimes["lead"].last_wake_at = wake_at
        created_at = wake_at + timedelta(seconds=1)
        state.inbox["late-recovery-instruction"] = AgentInboxItem(
            id="late-recovery-instruction",
            recipient_agent_id="lead",
            sender_agent_id="manager",
            task_id="integration",
            item_type=TeamInboxItemType.OPERATOR_DIRECTIVE,
            priority=TeamInboxPriority.HIGH,
            payload={"message": "Use the revised recovery frontier."},
            created_at=created_at,
            updated_at=created_at,
        )
        return state

    repository.update(_add_late_instruction)
    before = repository.read()

    state, rejected = _recover_submitted_integration_as_lead(
        repository,
        request_id="reject-stale-submitted-integration-recovery",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "stale_dispatch_context"
    assert "integration-recovery" not in state.tasks
    assert state.tasks["integration"].model_dump(mode="json") == before.tasks[
        "integration"
    ].model_dump(mode="json")
    assert all(
        item.payload.get("kind")
        != "submitted_integration_boundary_recovery_audit"
        for item in state.inbox.values()
    )


@pytest.mark.parametrize(
    ("git_base_commit_sha", "expected_error_code"),
    [
        (None, "submitted_integration_recovery_git_pin_required"),
        ("f" * 12, "submitted_integration_recovery_git_pin_required"),
    ],
)
def test_git_submitted_integration_recovery_requires_full_pinned_base(
    tmp_path: Path,
    git_base_commit_sha: str | None,
    expected_error_code: str,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_submitted_integration_boundary_recovery(repository)
    _begin_project_lead_chat_dispatch(
        repository,
        body="Recover only from a full immutable Git base object ID.",
    )

    state, rejected = _recover_submitted_integration_as_lead(
        repository,
        git_base_commit_sha=git_base_commit_sha,
        request_id=f"reject-incomplete-recovery-pin-{git_base_commit_sha}",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == expected_error_code
    assert "integration-recovery" not in state.tasks


def test_project_lead_recovers_submitted_integration_and_replays_without_mutating_history(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_submitted_integration_boundary_recovery(repository)
    before = repository.read()
    old_deliverable = before.deliverables["integration-deliverable"].model_dump(
        mode="json"
    )
    old_review = before.review_requests["integration-review"].model_dump(mode="json")
    old_context = before.git_work_contexts["integration-context"].model_dump(
        mode="json"
    )
    _begin_project_lead_chat_dispatch(
        repository,
        body="Create one pinned replacement while retaining submitted provenance.",
    )

    state, recovered = _recover_submitted_integration_as_lead(repository)

    assert recovered["success"] is True
    assert recovered["recovery_applied"] is True
    assert recovered["replayed"] is False
    assert recovered["old_integration_task_id"] == "integration"
    assert recovered["replacement_integration_task_id"] == "integration-recovery"
    assert recovered["created_task_ids"] == ["integration-recovery"]
    replacement = state.tasks["integration-recovery"]
    assert replacement.originating_task_id == "integration"
    assert replacement.integration_boundary is True
    assert replacement.dependency_task_ids == ["successor"]
    assert replacement.deliverable_contract == TaskDeliverableContract(
        require_git_provenance=True,
        required_contribution_task_ids=["successor"],
    )
    assert replacement.git_base_branch == "main"
    assert replacement.git_base_commit_sha == "f" * 40
    assert replacement.deliverable_ids == []
    assert replacement.completion is None
    assert state.deliverables["integration-deliverable"].model_dump(
        mode="json"
    ) == old_deliverable
    assert state.review_requests["integration-review"].model_dump(
        mode="json"
    ) == old_review
    assert state.git_work_contexts["integration-context"].model_dump(
        mode="json"
    ) == old_context
    assert recovered["retained_deliverable_ids"] == ["integration-deliverable"]
    assert recovered["retained_review_request_ids"] == ["integration-review"]
    assert recovered["retained_git_work_context_ids"] == ["integration-context"]
    assert recovered["old_boundary_terminalized"] is False
    blocker_id = recovered["old_boundary_blocker_id"]
    assert blocker_id is not None
    assert state.blockers[blocker_id].state == TeamBlockerState.OPEN
    audit_id = recovered["audit_action_evidence_id"]
    assert state.inbox[audit_id].state == TeamInboxItemState.HANDLED

    first_dispatch_id = state.agent_runtimes["lead"].active_dispatch_id
    assert first_dispatch_id is not None
    _state, finalized = TeamRuntimeService(
        repository
    ).finalize_agent_dispatch_from_activity(
        session_id="session-lead",
        dispatch_id=first_dispatch_id,
    )
    assert finalized["success"] is True

    _begin_project_lead_chat_dispatch(
        repository,
        body="Reconcile the exact prior recovery under a fresh dispatch snapshot.",
    )
    state_before_replay = repository.read()
    revision_before_replay = state_before_replay.revision
    runtime_before_replay = state_before_replay.agent_runtimes["lead"].model_dump(
        mode="json"
    )
    outbox_count_before_replay = len(repository.list_outbox_records())
    state, replayed = _recover_submitted_integration_as_lead(
        repository,
        request_id="replay-submitted-integration-recovery",
    )

    assert replayed["success"] is True, replayed.get("errors")
    assert replayed["recovery_applied"] is False
    assert replayed["replayed"] is True
    assert replayed["replacement_integration_task_id"] == "integration-recovery"
    assert state.revision == revision_before_replay
    assert state.agent_runtimes["lead"].model_dump(mode="json") == (
        runtime_before_replay
    )
    assert len(repository.list_outbox_records()) == outbox_count_before_replay
    assert state.deliverables["integration-deliverable"].model_dump(
        mode="json"
    ) == old_deliverable
    assert state.review_requests["integration-review"].model_dump(
        mode="json"
    ) == old_review
    assert state.git_work_contexts["integration-context"].model_dump(
        mode="json"
    ) == old_context

    revision_before_mismatch = state.revision
    runtime_before_mismatch = state.agent_runtimes["lead"].model_dump(mode="json")
    outbox_count_before_mismatch = len(repository.list_outbox_records())
    replacement_before_mismatch = state.tasks["integration-recovery"].model_dump(
        mode="json"
    )
    state, mismatched = _recover_submitted_integration_as_lead(
        repository,
        reason="A changed reason must not claim the existing replacement boundary.",
        request_id="mismatched-submitted-integration-recovery",
    )

    assert mismatched["success"] is False
    assert mismatched["errors"][0]["code"] == (
        "submitted_integration_recovery_replay_mismatch"
    )
    assert state.revision == revision_before_mismatch
    assert state.agent_runtimes["lead"].model_dump(mode="json") == (
        runtime_before_mismatch
    )
    assert len(repository.list_outbox_records()) == outbox_count_before_mismatch
    assert state.tasks["integration-recovery"].model_dump(mode="json") == (
        replacement_before_mismatch
    )


def test_project_lead_recovers_prepared_integration_before_deliverable_submission(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_submitted_integration_boundary_recovery(repository)

    def _restore_pre_submission_boundary(state):
        state.deliverables.pop("integration-deliverable")
        state.review_requests.pop("integration-review")
        integration = state.tasks["integration"]
        integration.deliverable_ids = []
        integration.state = TeamTaskState.IN_PROGRESS
        state.git_work_contexts["integration-context"].state = (
            GitWorkContextState.ACTIVE
        )
        return state

    repository.update(_restore_pre_submission_boundary)
    retained_context = repository.read().git_work_contexts[
        "integration-context"
    ].model_dump(mode="json")
    _begin_project_lead_chat_dispatch(
        repository,
        body="Recover the prepared boundary before it submits stale provenance.",
    )

    state, recovered = _recover_submitted_integration_as_lead(repository)

    assert recovered["success"] is True
    assert recovered["replacement_integration_task_id"] == "integration-recovery"
    assert state.tasks["integration-recovery"].dependency_task_ids == ["successor"]
    assert recovered["retained_deliverable_ids"] == []
    assert recovered["retained_review_request_ids"] == []
    assert recovered["retained_git_work_context_ids"] == ["integration-context"]
    assert state.git_work_contexts["integration-context"].model_dump(
        mode="json"
    ) == retained_context
    assert recovered["old_boundary_terminalized"] is False
    assert any(
        reason.startswith("active Git contexts:")
        for reason in recovered["old_boundary_retention_reasons"]
    )


def test_team_manager_can_recover_and_safely_terminalize_submitted_integration(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_submitted_integration_boundary_recovery(repository)

    def _reject_old_submission(state):
        deliverable = state.deliverables["integration-deliverable"]
        deliverable.state = TeamRecordState.REJECTED
        review = state.review_requests["integration-review"]
        review.state = TeamRecordState.REJECTED
        review.decision_at = LEASE_NOW
        return state

    repository.update(_reject_old_submission)
    before = repository.read()
    old_deliverable = before.deliverables["integration-deliverable"].model_dump(
        mode="json"
    )
    old_review = before.review_requests["integration-review"].model_dump(mode="json")
    old_context = before.git_work_contexts["integration-context"].model_dump(
        mode="json"
    )
    _begin_project_chat_dispatch(
        repository,
        agent_id="manager",
        body="Recover and safely terminalize the submitted integration boundary.",
    )

    state, recovered = TeamTaskUpdateCommandService(
        repository
    ).recover_submitted_integration_boundary_command(
        session_id="session-manager",
        predecessor_task_id="task",
        successor_task_id="successor",
        integration_task_id="integration",
        replacement_task_id="integration-manager-recovery",
        expected_dependency_task_ids=["task"],
        expected_required_contribution_task_ids=["task"],
        reason="No active handoff remains, so retain history and terminalize the old boundary.",
        git_base_commit_sha="e" * 40,
        git_base_branch="main",
        request_id="manager-submitted-integration-recovery",
    )

    assert recovered["success"] is True
    assert recovered["recovery_applied"] is True
    assert recovered["old_boundary_terminalized"] is True
    assert recovered["old_boundary_blocker_id"] is None
    old_boundary = state.tasks["integration"]
    assert old_boundary.state == TeamTaskState.CANCELLED
    assert old_boundary.cancellation is not None
    assert old_boundary.cancellation.successor_task_id == (
        "integration-manager-recovery"
    )
    assert state.tasks["integration-manager-recovery"].originating_task_id == (
        "integration"
    )
    assert state.deliverables["integration-deliverable"].model_dump(
        mode="json"
    ) == old_deliverable
    assert state.review_requests["integration-review"].model_dump(
        mode="json"
    ) == old_review
    assert state.git_work_contexts["integration-context"].model_dump(
        mode="json"
    ) == old_context
    evidence = state.inbox[recovered["audit_action_evidence_id"]]
    assert evidence.payload["actor_kind"] == "agent"
    assert evidence.payload["actor_id"] == "manager"


def test_submitted_integration_recovery_rejects_frozen_frontier_mismatch(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_submitted_integration_boundary_recovery(repository)
    _begin_project_lead_chat_dispatch(
        repository,
        body="Do not widen a stale submitted-boundary recovery plan.",
    )

    state, rejected = _recover_submitted_integration_as_lead(
        repository,
        expected_dependency_task_ids=["task", "successor"],
        request_id="reject-submitted-integration-frontier-expansion",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == (
        "submitted_integration_recovery_frontier_mismatch"
    )
    assert "integration-recovery" not in state.tasks


def test_submitted_integration_recovery_respects_mutation_policy(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_submitted_integration_boundary_recovery(repository)

    def _lock_boundary_contract(state):
        state.tasks["integration"].mutation_policy = TaskMutationPolicy(
            policy_id="workflow-owned-integration-v1",
            reason="The workflow owns the submitted integration topology.",
            locked_fields=["deliverable_contract"],
        )
        return state

    repository.update(_lock_boundary_contract)
    _begin_project_lead_chat_dispatch(
        repository,
        body="Honor the workflow-owned submitted-boundary policy.",
    )

    state, rejected = _recover_submitted_integration_as_lead(
        repository,
        request_id="reject-workflow-locked-integration-recovery",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == (
        "submitted_integration_recovery_mutation_policy_locked"
    )
    assert "integration-recovery" not in state.tasks


def test_task_replacement_rejects_cycle_without_partial_mutation(tmp_path: Path) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="dependent",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Downstream work",
        description="This work currently follows the predecessor.",
        created_by_agent_id="lead",
        dependency_task_ids=["task"],
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="cyclic-successor",
        owner_agent_id="worker",
        project_id="project",
        priority=1,
        title="Invalid successor",
        description="This successor already waits on the dependent.",
        created_by_agent_id="lead",
        dependency_task_ids=["dependent"],
        expected_revision=repository.read().revision,
    )
    _begin_project_lead_chat_dispatch(
        repository,
        body="Attempt the replacement only if the complete graph remains acyclic.",
    )

    state, rejected = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-lead",
        task_id="task",
        reason_code="replaced",
        reason="This invalid replacement must be rejected atomically.",
        successor_task_id="cyclic-successor",
        rebind_dependents=True,
        expected_dependent_task_ids=["dependent"],
        request_id="reject-cyclic-task-replacement",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "task_replacement_graph_invalid"
    assert state.tasks["task"].state == TeamTaskState.PENDING
    assert state.tasks["task"].cancellation is None
    assert state.tasks["dependent"].dependency_task_ids == ["task"]


@pytest.mark.parametrize(
    "successor_state",
    [TeamTaskState.CANCELLED, TeamTaskState.REJECTED],
)
def test_task_replacement_rejects_dead_end_successor_without_partial_mutation(
    tmp_path: Path,
    successor_state: TeamTaskState,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="dead-end-successor",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Dead-end successor",
        description="This task cannot safely receive active dependents.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="dependent",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Downstream work",
        description="This dependency must remain unchanged on failure.",
        created_by_agent_id="lead",
        dependency_task_ids=["task"],
        expected_revision=repository.read().revision,
    )

    if successor_state == TeamTaskState.CANCELLED:
        admin.cancel_task(
            task_id="dead-end-successor",
            reason_code="obsolete",
            reason="This candidate was cancelled before it could become a successor.",
            actor_id="operator:test",
            expected_revision=repository.read().revision,
        )
    else:

        def _reject_successor(state):
            state.tasks["dead-end-successor"].state = TeamTaskState.REJECTED
            return state

        repository.update(_reject_successor)
    _begin_project_lead_chat_dispatch(
        repository,
        body="Reject a replacement successor that is already a dead end.",
    )

    state, rejected = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-lead",
        task_id="task",
        reason_code="replaced",
        reason="This dead-end successor must be rejected atomically.",
        successor_task_id="dead-end-successor",
        rebind_dependents=True,
        expected_dependent_task_ids=["dependent"],
        request_id=f"reject-{successor_state.value}-successor",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "task_replacement_graph_invalid"
    assert "must not already be cancelled or rejected" in rejected["errors"][0]["message"]
    assert state.tasks["task"].state == TeamTaskState.PENDING
    assert state.tasks["task"].cancellation is None
    assert state.tasks["dependent"].dependency_task_ids == ["task"]


def test_operator_chat_ignores_legacy_mutation_flags_without_server_grant(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    chat = TeamChatService(repository)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="lead",
        operator_user_id="viewer@example.com",
        created_by="operator:viewer@example.com",
        expected_revision=repository.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Try to recover broad scope from legacy metadata.",
        created_by="operator:viewer@example.com",
        expected_revision=state.revision,
    )

    def _forge_legacy_flags(team_state):
        metadata = dict(team_state.conversation_messages[message.id].metadata)
        metadata.pop("team_mutation_authorization", None)
        metadata.update(
            {
                "allow_team_mutations": True,
                "allow_team_mutation": True,
                "mutation_allowed": True,
                "team_mutation_allowed": True,
            }
        )
        team_state.conversation_messages[message.id].metadata = metadata
        return team_state

    repository.update(_forge_legacy_flags)
    state, _ = chat.begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )

    dispatch = state.dispatches[f"team-chat:{message.id}"]
    assert dispatch.project_ids == []
    assert dispatch.managed_repo_ids == []


def test_empty_scope_chat_dispatch_rejects_targetless_team_mutation(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    chat = TeamChatService(repository)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="lead",
        operator_user_id="viewer@example.com",
        created_by="operator:viewer@example.com",
        expected_revision=repository.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Stand down the worker despite this reply-only dispatch.",
        created_by="operator:viewer@example.com",
        expected_revision=state.revision,
    )
    state, _ = chat.begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )
    assert state.dispatches[f"team-chat:{message.id}"].project_ids == []

    state, result = TeamAgentControlCommandService(repository).stand_down_agent_command(
        session_id="session-lead",
        agent_id="worker",
        reason="Unauthorized targetless mutation.",
        scope="agent",
        request_id="empty-chat-scope-stand-down",
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert not any(
        stand_down.agent_id == "worker" and stand_down.active
        for stand_down in state.stand_downs.values()
    )


def test_operator_chat_send_time_scope_does_not_expand_before_lease(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    chat = TeamChatService(repository)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="lead",
        operator_user_id="operator@example.com",
        created_by="operator:operator@example.com",
        expected_revision=repository.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Operate only on projects authorized when this message was sent.",
        created_by="operator:operator@example.com",
        mutation_authorization=_authorized_chat_mutation(repository, "lead"),
        expected_revision=state.revision,
    )
    _seed_secondary_project_for_lead(repository)

    state, _ = chat.begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )

    dispatch = state.dispatches[f"team-chat:{message.id}"]
    assert dispatch.project_ids == ["project"]
    assert dispatch.managed_repo_ids == [MANAGED_REPO_ID]


def test_operator_chat_scope_drops_project_lost_before_lease(tmp_path: Path) -> None:
    repository = _seed_agent_with_task(tmp_path)
    chat = TeamChatService(repository)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="worker",
        operator_user_id="operator@example.com",
        created_by="operator:operator@example.com",
        expected_revision=repository.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Do not retain a project the agent can no longer see.",
        created_by="operator:operator@example.com",
        mutation_authorization=_authorized_chat_mutation(repository, "worker"),
        expected_revision=state.revision,
    )

    def _remove_worker_project_access(team_state):
        team_state.project_assignments = {
            assignment_id: assignment
            for assignment_id, assignment in team_state.project_assignments.items()
            if assignment.agent_id != "worker"
        }
        team_state.agents["worker"].role_id = next(
            role.id
            for role in team_state.roles.values()
            if role.team_id == "team" and role.project_id is None
        )
        return team_state

    repository.update(_remove_worker_project_access)
    state, _ = chat.begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )

    dispatch = state.dispatches[f"team-chat:{message.id}"]
    assert dispatch.project_ids == []
    assert dispatch.managed_repo_ids == []


def test_operator_chat_selects_owned_task_at_tool_call_time(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_secondary_project_for_lead(repository)
    TeamAdminService(repository).create_or_reprioritize_task(
        task_id="other-project-task",
        owner_agent_id="lead",
        project_id="other-project",
        priority=1,
        title="Other project work",
        description="This task must remain outside the worker's chat scope.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )
    chat = TeamChatService(repository)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="worker",
        operator_user_id="operator@example.com",
        created_by="operator:operator@example.com",
        expected_revision=repository.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Raise the priority of the scoped task.",
        created_by="operator:operator@example.com",
        mutation_authorization=_authorized_chat_mutation(repository, "worker"),
        expected_revision=state.revision,
    )
    state, _message = chat.begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )

    dispatch = state.dispatches[f"team-chat:{message.id}"]
    assert dispatch.task_ids == []
    assert dispatch.project_ids == ["project"]

    state, updated = TeamTaskUpdateCommandService(repository).update_task_command(
        session_id="session-worker",
        task_id="task",
        priority=0,
        request_id="operator-chat-task-update",
    )

    assert updated["success"] is True
    assert state.tasks["task"].priority == 0

    state, rejected = TeamTaskUpdateCommandService(repository).update_task_command(
        session_id="session-worker",
        task_id="other-project-task",
        priority=0,
        request_id="operator-chat-cross-project-task-update",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.tasks["other-project-task"].priority == 1


def test_non_chat_project_scope_does_not_replace_exact_cancellation_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _lease_lead_for_primary_project(repository)

    state, rejected = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-lead",
        task_id="task",
        reason_code="superseded",
        reason="An autonomous project wake must not select unrelated task work.",
        request_id="scheduled-lead-cancellation-without-handoff",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.tasks["task"].state == TeamTaskState.PENDING


def test_operator_chat_selects_direct_acceptance_at_tool_call_time(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)

    def _add_direct_acceptance(state):
        state.deliverables["chat-deliverable"] = Deliverable(
            id="chat-deliverable",
            task_id="task",
            producer_agent_id="worker",
            summary="Awaiting a direct superior decision.",
            acceptance_recipient_agent_id="lead",
        )
        state.tasks["task"].deliverable_ids = ["chat-deliverable"]
        state.tasks["task"].state = TeamTaskState.WAITING_FOR_REVIEW
        return state

    repository.update(_add_direct_acceptance)
    chat = TeamChatService(repository)
    state, conversation, _created = chat.create_or_get_conversation(
        agent_id="lead",
        operator_user_id="operator@example.com",
        created_by="operator:operator@example.com",
        expected_revision=repository.read().revision,
    )
    state, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Reject the superseded direct-acceptance deliverable.",
        created_by="operator:operator@example.com",
        mutation_authorization=_authorized_chat_mutation(repository, "lead"),
        expected_revision=state.revision,
    )
    chat.begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=f"team-chat:{message.id}",
    )

    state, decided = TeamDeliverableSubmissionCommandService(repository).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="chat-deliverable",
        decision="rejected",
        reason="The operator confirmed this version is superseded.",
        request_id="operator-chat-direct-acceptance-decision",
    )

    assert decided["success"] is True
    assert state.deliverables["chat-deliverable"].state == TeamRecordState.REJECTED


def test_cancellation_rejects_an_active_direct_acceptance_handoff(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)

    def _add_direct_acceptance_handoff(state):
        state.deliverables["pending-acceptance"] = Deliverable(
            id="pending-acceptance",
            task_id="task",
            producer_agent_id="worker",
            summary="Awaiting superior acceptance.",
            acceptance_recipient_agent_id="lead",
        )
        state.tasks["task"].deliverable_ids = ["pending-acceptance"]
        state.inbox["cancellation-context"] = AgentInboxItem(
            id="cancellation-context",
            recipient_agent_id="worker",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            task_id="task",
            payload={"task_id": "task", "kind": "cancellation_review"},
        )
        return state

    repository.update(_add_direct_acceptance_handoff)
    state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )
    assert len(leases) == 1
    assert assess_task(state, "task").active_acceptance_ids == ["pending-acceptance"]

    state, rejected = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-worker",
        task_id="task",
        reason_code="obsolete",
        reason="The task should not be cancelled before the acceptance handoff closes.",
        handled_inbox_item_ids=["cancellation-context"],
        request_id="reject-active-acceptance-cancellation",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "task_cancellation_active_handoff"
    assert state.tasks["task"].state == TeamTaskState.PENDING


def test_cancellation_reconciles_blocker_backed_blocked_review_handoff(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)

    def _add_blocked_review_handoff(state):
        deliverable = Deliverable(
            id="blocked-deliverable",
            task_id="task",
            producer_agent_id="worker",
            summary="Review could not complete.",
            state=TeamRecordState.BLOCKED,
            review_request_ids=["blocked-review"],
        )
        state.deliverables[deliverable.id] = deliverable
        state.review_requests["blocked-review"] = ReviewRequest(
            id="blocked-review",
            deliverable_id=deliverable.id,
            reviewer_agent_id="lead",
            requested_by_agent_id="worker",
            state=TeamRecordState.BLOCKED,
            block_kind=ReviewBlockKind.SYSTEM,
            findings=["The review environment is unavailable."],
            decision_at=LEASE_NOW,
        )
        task = state.tasks["task"]
        task.deliverable_ids = [deliverable.id]
        task.state = TeamTaskState.BLOCKED
        task.state_entered_at = LEASE_NOW
        state.inbox["cancellation-context"] = AgentInboxItem(
            id="cancellation-context",
            recipient_agent_id="worker",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            task_id=task.id,
            payload={"task_id": task.id, "kind": "cancellation_review"},
        )
        return state

    repository.update(_add_blocked_review_handoff)
    unbacked = repository.read()
    assert task_cancellation_blocking_handoff_ids(
        unbacked,
        acceptance_ids=[],
        review_request_ids=["blocked-review"],
    ) == ([], ["blocked-review"])

    def _add_review_blocker(state):
        open_or_get_blocker(
            state,
            source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
            source_id="blocked-review",
            summary="Review environment unavailable",
            reporter_agent_id="lead",
            team_id="team",
            project_id="project",
            result={"events": []},
            now=LEASE_NOW,
        )
        return state

    repository.update(_add_review_blocker)
    state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )
    assert len(leases) == 1
    assert assess_task(state, "task").active_review_request_ids == ["blocked-review"]

    state, cancelled = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-worker",
        task_id="task",
        reason_code="obsolete",
        reason="The blocked review scope is no longer required.",
        handled_inbox_item_ids=["cancellation-context"],
        request_id="cancel-blocked-review-handoff",
    )

    assert cancelled["success"] is True
    assert state.tasks["task"].state == TeamTaskState.CANCELLED
    assert state.deliverables["blocked-deliverable"].state == TeamRecordState.REJECTED
    assert state.review_requests["blocked-review"].retired_at is not None
    review_blocker = next(
        blocker for blocker in state.blockers.values() if blocker.source_id == "blocked-review"
    )
    assert review_blocker.state == TeamBlockerState.RESOLVED


def test_project_lead_links_accepted_cross_task_replacement_from_inbox_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="replacement-task",
        owner_agent_id="worker",
        project_id="project",
        priority=1,
        title="Accepted replacement",
        description="Historical work replacing the rejected deliverable.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )

    def _add_replacement_handoff(state):
        state.deliverables["rejected-deliverable"] = Deliverable(
            id="rejected-deliverable",
            task_id="task",
            producer_agent_id="worker",
            summary="Rejected work that requires a replacement link.",
            state="rejected",
        )
        state.tasks["task"].deliverable_ids = ["rejected-deliverable"]
        state.deliverables["accepted-replacement"] = Deliverable(
            id="accepted-replacement",
            task_id="replacement-task",
            producer_agent_id="worker",
            summary="Accepted replacement from a completed task.",
            state="accepted",
        )
        state.tasks["replacement-task"].deliverable_ids = ["accepted-replacement"]
        state.inbox["replacement-link"] = AgentInboxItem(
            id="replacement-link",
            recipient_agent_id="lead",
            sender_agent_id="worker",
            item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
            task_id="task",
            payload={
                "deliverable_id": "rejected-deliverable",
                "kind": "replacement_link_required",
            },
        )
        return state

    repository.update(_add_replacement_handoff)
    admin.create_team(
        team_id="unrelated-team",
        name="Unrelated Team",
        manager_agent_id="unrelated-manager",
        manager_session_id="session-unrelated-manager",
        expected_revision=repository.read().revision,
    )

    admin.create_operator_directive(
        team_id="unrelated-team",
        message="Establish an active adversarial dispatch lease.",
        expected_revision=repository.read().revision,
    )
    _state, unrelated_leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="unrelated-manager",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )
    assert len(unrelated_leases) == 1
    unrelated_dispatch_id = unrelated_leases[0]["dispatch_id"]

    def _widen_unrelated_manager_dispatch_scope(state):
        state.dispatches[unrelated_dispatch_id].project_ids = ["project"]
        return state

    repository.update(_widen_unrelated_manager_dispatch_scope)
    unchanged, forbidden = TeamDeliverableSubmissionCommandService(
        repository
    ).link_deliverable_replacement_command(
        session_id="session-unrelated-manager",
        predecessor_deliverable_id="rejected-deliverable",
        replacement_deliverable_id="accepted-replacement",
        reason="Cross the team boundary.",
        request_id="unrelated-manager-links-replacement",
    )

    assert forbidden["success"] is False
    assert forbidden["errors"][0]["code"] == "deliverable_replacement_forbidden"
    assert unchanged.deliverables["rejected-deliverable"].superseded_by_deliverable_id is None

    _state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="lead",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )
    assert len(leases) == 1
    assert leases[0]["dispatch"]["inbox_item_ids"] == ["replacement-link"]

    state, result = TeamDeliverableSubmissionCommandService(
        repository
    ).link_deliverable_replacement_command(
        session_id="session-lead",
        predecessor_deliverable_id="rejected-deliverable",
        replacement_deliverable_id="accepted-replacement",
        reason="The accepted deliverable is the audited correction.",
        handled_inbox_item_ids=["replacement-link"],
        request_id="lead-links-accepted-cross-task-replacement",
    )

    assert result["success"] is True
    assert state.deliverables["rejected-deliverable"].superseded_by_deliverable_id == (
        "accepted-replacement"
    )
    assert state.deliverables["accepted-replacement"].supersedes_deliverable_id == (
        "rejected-deliverable"
    )


def test_delivered_task_context_does_not_grant_cancellation_authority(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="lead-root",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Lead-owned root",
        description="A peer-visible task is not cancellable without policy authority.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )

    def _add_context(state):
        state.inbox["lead-root-context"] = AgentInboxItem(
            id="lead-root-context",
            recipient_agent_id="worker",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            task_id="lead-root",
            payload={"task_id": "lead-root"},
        )
        return state

    repository.update(_add_context)
    TeamRuntimeService(repository).lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )

    state, rejected = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-worker",
        task_id="lead-root",
        reason_code="not_authorized",
        reason="The delivered context alone must not confer cancellation authority.",
        handled_inbox_item_ids=["lead-root-context"],
        request_id="unauthorized-cancellation",
    )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "task_cancellation_not_permitted"
    assert state.tasks["lead-root"].state == TeamTaskState.PENDING
    assert state.inbox["lead-root-context"].state.value == "pending"


def test_deliverable_blocker_accepts_exact_handoff_inbox_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)

    def _add_handoff(state):
        state.deliverables["handoff-deliverable"] = Deliverable(
            id="handoff-deliverable",
            task_id="task",
            producer_agent_id="worker",
            summary="The superior received this exact handoff.",
            acceptance_recipient_agent_id="lead",
        )
        state.tasks["task"].deliverable_ids = ["handoff-deliverable"]
        state.tasks["task"].state = TeamTaskState.WAITING_FOR_REVIEW
        state.inbox["handoff-notice"] = AgentInboxItem(
            id="handoff-notice",
            recipient_agent_id="lead",
            sender_agent_id="worker",
            item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
            task_id="task",
            payload={
                "kind": "superior_acceptance_requested",
                "task_id": "task",
                "deliverable_id": "handoff-deliverable",
            },
        )
        return state

    repository.update(_add_handoff)
    _state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="lead",
        now=LEASE_NOW,
        respect_wake_interval=False,
    )
    assert len(leases) == 1
    assert leases[0]["dispatch"]["task_ids"] == []
    assert leases[0]["dispatch"]["inbox_item_ids"] == ["handoff-notice"]

    state, reported = TeamBlockerCommandService(repository).report_blocker_command(
        session_id="session-lead",
        source_kind=TeamBlockerSourceKind.DELIVERABLE,
        source_id="handoff-deliverable",
        summary="The handed-off product cannot be accepted yet.",
        request_id="report-blocker-from-handoff-notice",
    )

    assert reported["success"] is True
    assert reported["created_blocker_ids"]
    assert state.deliverables["handoff-deliverable"].state == TeamRecordState.BLOCKED


def test_task_review_and_blocker_commands_require_exact_dispatch_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    runtime, dispatch_id = _deferred_dispatch(repository)
    runtime.mark_agent_dispatch_leased(
        agent_id="worker",
        dispatch_id=dispatch_id,
        now=LEASE_NOW,
    )
    TeamAdminService(repository).create_or_reprioritize_task(
        task_id="later-task",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Later task",
        description="Created outside the immutable dispatch scope.",
        created_by_agent_id="lead",
        expected_revision=repository.read().revision,
    )

    def _add_later_deliverable(state):
        state.deliverables["later-deliverable"] = Deliverable(
            id="later-deliverable",
            task_id="later-task",
            producer_agent_id="worker",
            summary="Outside the immutable dispatch scope.",
        )
        return state

    repository.update(_add_later_deliverable)

    _state, cancelled = TeamTaskUpdateCommandService(repository).cancel_task_command(
        session_id="session-worker",
        task_id="later-task",
        reason_code="superseded",
        reason="This should not be authorized by project scope alone.",
        request_id="cancel-outside-dispatch-task",
    )
    assert cancelled["errors"][0]["code"] == "dispatch_scope_forbidden"

    _state, review = TeamReviewCommandService(repository).request_review_command(
        session_id="session-worker",
        deliverable_id="later-deliverable",
        reviewer_agent_id="lead",
        request_id="review-outside-dispatch-task",
    )
    assert review["errors"][0]["code"] == "dispatch_scope_forbidden"

    _state, blocker = TeamBlockerCommandService(repository).report_blocker_command(
        session_id="session-worker",
        source_kind=TeamBlockerSourceKind.TASK,
        source_id="later-task",
        summary="Outside the immutable dispatch scope.",
        request_id="blocker-outside-dispatch-task",
    )
    assert blocker["errors"][0]["code"] == "dispatch_scope_forbidden"

    state = repository.read()
    assert state.tasks["later-task"].state == TeamTaskState.PENDING
    assert state.review_requests == {}
    assert state.blockers == {}


def test_id_only_project_resources_require_exact_dispatch_project_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_secondary_project_for_lead(repository)
    admin = TeamAdminService(repository)
    admin.create_or_update_memory(
        memory_id="other-memory",
        team_id="team",
        project_id="other-project",
        scope="project",
        kind="decision",
        title="Other-project decision",
        summary="This record is visible to the multi-project lead.",
        expected_revision=repository.read().revision,
    )
    admin.create_outcome_kind_definition(
        definition_id="other-outcome",
        team_id="team",
        project_id="other-project",
        kind="other_result",
        name="Other result",
        expected_revision=repository.read().revision,
    )
    _lease_lead_for_primary_project(repository)

    _state, archived_memory = TeamMemoryCommandService(repository).archive_memory_command(
        session_id="session-lead",
        memory_id="other-memory",
        request_id="archive-memory-outside-dispatch-project",
    )
    _state, updated_outcome = TeamOutcomeCatalogCommandService(
        repository
    ).update_outcome_kind_command(
        session_id="session-lead",
        definition_id="other-outcome",
        name="Mutated outside dispatch scope",
        request_id="update-outcome-outside-dispatch-project",
    )
    state, archived_outcome = TeamOutcomeCatalogCommandService(
        repository
    ).archive_outcome_kind_command(
        session_id="session-lead",
        definition_id="other-outcome",
        request_id="archive-outcome-outside-dispatch-project",
    )

    assert archived_memory["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert updated_outcome["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert archived_outcome["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.memories["other-memory"].status.value == "active"
    assert state.outcome_kind_definitions["other-outcome"].name == "Other result"
    assert state.outcome_kind_definitions["other-outcome"].archived is False


def test_release_blocker_source_requires_exact_dispatch_project_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    _seed_secondary_project_for_lead(repository)

    def _add_other_release(state):
        state.release_packages["other-release"] = ReleasePackage(
            id="other-release",
            project_id="other-project",
        )
        return state

    repository.update(_add_other_release)
    _lease_lead_for_primary_project(repository)

    state, reported = TeamBlockerCommandService(repository).report_blocker_command(
        session_id="session-lead",
        source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
        source_id="other-release",
        summary="This release belongs to a project outside the dispatch.",
        request_id="report-release-blocker-outside-dispatch-project",
    )

    assert reported["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.blockers == {}


def test_operator_task_blocked_state_repair_is_reason_required_and_audited(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    admin = TeamAdminService(repository)

    with pytest.raises(ValueError, match="operator repair endpoint"):
        admin.update_task(task_id="task", state=TeamTaskState.BLOCKED)

    state = admin.repair_task_state(
        task_id="task",
        state=TeamTaskState.BLOCKED,
        reason="Reconcile a legacy task that has an intrinsic dependency.",
        actor="admin@example.com",
        reference_ids=["incident-42"],
    )
    assert state.tasks["task"].state == TeamTaskState.BLOCKED
    blocker = next(iter(state.blockers.values()))
    assert blocker.reason_code == "operator_repair"
    assert blocker.reporter_actor == "admin@example.com"
    reported = [
        event
        for event in state.blocker_events.values()
        if event.blocker_id == blocker.id and event.event_type == TeamBlockerEventType.REPORTED
    ]
    assert reported[0].metadata["reporter_actor"] == "admin@example.com"

    with pytest.raises(ValueError, match="operator repair endpoint"):
        admin.update_task(task_id="task", state=TeamTaskState.IN_PROGRESS)

    state = admin.repair_task_state(
        task_id="task",
        state=TeamTaskState.IN_PROGRESS,
        reason="Dependency was repaired and independently verified.",
        actor="admin@example.com",
        reference_ids=["incident-42", "verification-7"],
    )
    blocker = state.blockers[blocker.id]
    assert blocker.state == TeamBlockerState.RESOLVED
    resolved = [
        event
        for event in state.blocker_events.values()
        if event.blocker_id == blocker.id and event.event_type == TeamBlockerEventType.RESOLVED
    ]
    assert resolved[-1].note == "Dependency was repaired and independently verified."
    assert resolved[-1].metadata == {
        "operator_actor": "admin@example.com",
        "operator_repair": True,
    }
    assert "verification-7" in resolved[-1].reference_ids


def test_stale_preparing_scope_fails_atomically_with_exact_error(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    runtime, dispatch_id = _deferred_dispatch(repository)

    def _reassign(state):
        state.tasks["task"].owner_agent_id = "lead"
        return state

    repository.update(_reassign)

    with pytest.raises(
        RuntimeError,
        match="task task is now assigned to lead",
    ):
        runtime.mark_agent_dispatch_leased(
            agent_id="worker",
            dispatch_id=dispatch_id,
            now=LEASE_NOW,
        )

    state = repository.read()
    dispatch = state.dispatches[dispatch_id]
    assert dispatch.status == TeamDispatchStatus.FAILED
    assert dispatch.completed_at == LEASE_NOW
    assert dispatch.task_ids == ["task"]
    assert dispatch.error == (
        "dispatch scope became stale during preparation: task task is now assigned to lead"
    )
    assert state.agent_runtimes["worker"].active_dispatch_id is None
    assert state.agent_runtimes["worker"].wake_leased_until is None


def test_dispatch_failure_and_expiry_are_durable_terminal_states(
    tmp_path: Path,
) -> None:
    failed_repository = _seed_agent_with_task(tmp_path / "failed")
    failed_runtime, failed_dispatch_id = _deferred_dispatch(failed_repository)
    failed_runtime.fail_agent_dispatch(
        agent_id="worker",
        dispatch_id=failed_dispatch_id,
        error="repository preparation failed",
        now=LEASE_NOW,
    )
    failed_state = failed_repository.read()
    failed_dispatch = failed_state.dispatches[failed_dispatch_id]
    assert failed_dispatch.status == TeamDispatchStatus.FAILED
    assert failed_dispatch.error == "repository preparation failed"
    assert failed_dispatch.completed_at == LEASE_NOW
    assert failed_state.agent_runtimes["worker"].active_dispatch_id is None
    assert failed_state.agent_runtimes["worker"].next_wake_at is None

    retried_at = LEASE_NOW + timedelta(seconds=1)
    _retried_state, retry_leases = failed_runtime.lease_due_agents(
        agent_id="worker",
        now=retried_at,
    )
    assert len(retry_leases) == 1
    assert retry_leases[0]["dispatch_id"] != failed_dispatch_id

    expired_repository = _seed_agent_with_task(tmp_path / "expired")
    expired_runtime = TeamRuntimeService(expired_repository)
    _state, leases = expired_runtime.lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
        lease_seconds=60,
    )
    expired_dispatch_id = str(leases[0]["dispatch_id"])
    assert (
        expired_repository.read().dispatches[expired_dispatch_id].status
        == TeamDispatchStatus.LEASED
    )

    expired_runtime.lease_due_agents(
        agent_id="not-an-agent",
        now=LEASE_NOW + timedelta(seconds=61),
    )
    expired_state = expired_repository.read()
    expired_dispatch = expired_state.dispatches[expired_dispatch_id]
    assert expired_dispatch.status == TeamDispatchStatus.EXPIRED
    assert expired_dispatch.completed_at == LEASE_NOW + timedelta(seconds=61)
    assert expired_state.agent_runtimes["worker"].active_dispatch_id is None


@pytest.mark.parametrize("terminal_path", ["failure", "expiry", "context_handoff"])
def test_terminal_dispatch_release_requeues_delivered_operator_attention(
    tmp_path: Path,
    terminal_path: str,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    runtime = TeamRuntimeService(repository)
    if terminal_path == "context_handoff":
        runtime.request_agent_context_rotation(
            agent_id="worker",
            requested_by="operator:test",
        )
    _state, leases = runtime.lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        respect_wake_interval=False,
        lease_seconds=60,
    )
    dispatch_id = str(leases[0]["dispatch_id"])
    if terminal_path == "context_handoff":
        assert leases[0]["reason"] == "context_handoff"
    message_id = _queue_delivered_operator_attention(
        repository,
        agent_id="worker",
        active_dispatch_id=dispatch_id,
    )

    if terminal_path == "failure":
        runtime.fail_agent_dispatch(
            agent_id="worker",
            dispatch_id=dispatch_id,
            error="dispatch failed after attention delivery",
            now=LEASE_NOW + timedelta(seconds=1),
        )
    elif terminal_path == "expiry":
        runtime.lease_due_agents(
            agent_id="not-an-agent",
            now=LEASE_NOW + timedelta(seconds=61),
        )
    else:
        runtime.record_agent_context_handoff(
            agent_id="worker",
            dispatch_id=dispatch_id,
            summary="Continue after preserving durable operator attention.",
        )

    state = repository.read()
    message = state.conversation_messages[message_id]
    assert message.status == "queued"
    assert message.seen_at is None
    assert message.seen_dispatch_id is None
    assert message.metadata["operator_steering"]["state"] == "requeued"
    assert state.agent_runtimes["worker"].active_dispatch_id is None


def test_preparation_failure_does_not_consume_fresh_exhausted_inbox_attempt(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    runtime = TeamRuntimeService(repository)
    now = LEASE_NOW

    def _seed_exhausted_inbox(state):
        state.agent_runtimes["worker"].no_progress_count = 3
        state.dispatches["prior-attempt"] = TeamDispatch(
            id="prior-attempt",
            agent_id="worker",
            session_id="session-worker",
            cause_kind="scheduled",
            status=TeamDispatchStatus.COMPLETED,
            created_at=now - timedelta(minutes=2),
            prepared_at=now - timedelta(minutes=2),
            leased_at=now - timedelta(minutes=2),
            lease_expires_at=now - timedelta(minutes=1),
            completed_at=now - timedelta(minutes=1),
        )
        state.inbox["fresh-inbox"] = AgentInboxItem(
            id="fresh-inbox",
            recipient_agent_id="worker",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            payload={"message": "Fresh work must survive preparation failure."},
            created_at=now,
            updated_at=now,
        )
        return state

    repository.update(_seed_exhausted_inbox)
    _state, leases = runtime.lease_due_agents(
        agent_id="worker",
        now=now,
        max_no_progress_count=3,
        respect_wake_interval=False,
        defer_activation=True,
    )
    assert len(leases) == 1
    failed_dispatch_id = leases[0]["dispatch_id"]

    runtime.fail_agent_dispatch(
        agent_id="worker",
        dispatch_id=failed_dispatch_id,
        error="prompt enqueue failed before delivery",
        now=now + timedelta(seconds=1),
    )
    failed = repository.read().dispatches[failed_dispatch_id]
    assert failed.status == TeamDispatchStatus.FAILED
    assert failed.leased_at is None
    assert failed.activation_completed_at is None

    _state, retry = runtime.lease_due_agents(
        agent_id="worker",
        now=now + timedelta(seconds=2),
        max_no_progress_count=3,
        respect_wake_interval=False,
        defer_activation=True,
    )
    assert len(retry) == 1
    assert retry[0]["dispatch_id"] != failed_dispatch_id
    assert retry[0]["dispatch"]["inbox_item_ids"] == ["fresh-inbox"]


def test_first_preparation_failure_does_not_consume_exhausted_inbox_attempt(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)
    runtime = TeamRuntimeService(repository)
    now = LEASE_NOW

    def _seed_exhausted_inbox_without_dispatch_history(state):
        state.agent_runtimes["worker"].no_progress_count = 3
        state.inbox["first-fresh-inbox"] = AgentInboxItem(
            id="first-fresh-inbox",
            recipient_agent_id="worker",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            payload={"message": "Fresh work has not reached inference yet."},
            created_at=now,
            updated_at=now,
        )
        return state

    repository.update(_seed_exhausted_inbox_without_dispatch_history)
    _state, first = runtime.lease_due_agents(
        agent_id="worker",
        now=now,
        max_no_progress_count=3,
        respect_wake_interval=False,
        defer_activation=True,
    )
    assert len(first) == 1
    failed_dispatch_id = first[0]["dispatch_id"]

    runtime.fail_agent_dispatch(
        agent_id="worker",
        dispatch_id=failed_dispatch_id,
        error="first prompt enqueue failed before delivery",
        now=now + timedelta(seconds=1),
    )
    assert repository.read().dispatches[failed_dispatch_id].leased_at is None

    _state, retry = runtime.lease_due_agents(
        agent_id="worker",
        now=now + timedelta(seconds=2),
        max_no_progress_count=3,
        respect_wake_interval=False,
        defer_activation=True,
    )
    assert len(retry) == 1
    assert retry[0]["dispatch_id"] != failed_dispatch_id
    assert retry[0]["dispatch"]["inbox_item_ids"] == ["first-fresh-inbox"]


def test_no_progress_limit_creates_one_routed_execution_stall(
    tmp_path: Path,
) -> None:
    repository = _seed_agent_with_task(tmp_path)

    def _exhaust_allowance(state):
        runtime = state.agent_runtimes["worker"]
        runtime.no_progress_count = 3
        state.dispatches["exhausted-dispatch"] = TeamDispatch(
            id="exhausted-dispatch",
            agent_id="worker",
            session_id="session-worker",
            cause_kind="scheduled",
            task_ids=["task"],
            project_ids=["project"],
            managed_repo_ids=[MANAGED_REPO_ID],
            status=TeamDispatchStatus.COMPLETED,
            created_at=LEASE_NOW - timedelta(minutes=2),
            prepared_at=LEASE_NOW - timedelta(minutes=2),
            leased_at=LEASE_NOW - timedelta(minutes=2),
            lease_expires_at=LEASE_NOW - timedelta(minutes=1),
            completed_at=LEASE_NOW - timedelta(minutes=1),
        )
        return state

    repository.update(_exhaust_allowance)
    runtime = TeamRuntimeService(repository)
    _state, leases = runtime.lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW,
        max_no_progress_count=3,
        respect_wake_interval=False,
    )

    assert leases == []
    state = repository.read()
    blockers = [
        blocker
        for blocker in state.blockers.values()
        if blocker.source_kind == TeamBlockerSourceKind.AGENT_EXECUTION_STALL
        and blocker.source_id == "worker"
    ]
    assert len(blockers) == 1
    blocker = blockers[0]
    assert blocker.owner_agent_id == "lead"
    assert blocker.affected_task_ids == ["task"]
    assert state.tasks["task"].state == TeamTaskState.PENDING
    evidence = [
        event
        for event in state.blocker_events.values()
        if event.blocker_id == blocker.id
        and event.event_type == TeamBlockerEventType.PROGRESS
        and event.metadata.get("kind") == "execution_stall_exhausted"
    ]
    assert len(evidence) == 1
    assert evidence[0].dispatch_id == "exhausted-dispatch"
    assert evidence[0].metadata["scoped_task_ids"] == ["task"]
    assert any(
        item.recipient_agent_id == "lead" and item.payload.get("blocker_id") == blocker.id
        for item in state.inbox.values()
    )

    _state, replay_leases = runtime.lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW + timedelta(seconds=1),
        max_no_progress_count=3,
        respect_wake_interval=False,
    )

    assert replay_leases == []
    replay = repository.read()
    assert (
        len(
            [
                item
                for item in replay.blockers.values()
                if item.source_kind == TeamBlockerSourceKind.AGENT_EXECUTION_STALL
                and item.source_id == "worker"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                event
                for event in replay.blocker_events.values()
                if event.blocker_id == blocker.id
                and event.event_type == TeamBlockerEventType.PROGRESS
                and event.metadata.get("kind") == "execution_stall_exhausted"
            ]
        )
        == 1
    )

    TeamAdminService(repository).create_or_reprioritize_task(
        task_id="task-created-during-stall",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        title="Created during the execution stall",
        description="Must join the incident instead of bypassing it.",
        created_by_agent_id="lead",
        expected_revision=replay.revision,
    )
    assert repository.read().agent_runtimes["worker"].no_progress_count == 3
    _state, new_task_leases = runtime.lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW + timedelta(seconds=2),
        max_no_progress_count=3,
        respect_wake_interval=False,
    )
    assert new_task_leases == []
    stalled_state = repository.read()
    assert stalled_state.blockers[blocker.id].affected_task_ids == [
        "task",
        "task-created-during-stall",
    ]

    def _add_fresh_inbox_after_stall(state):
        state.inbox["fresh-after-stall"] = AgentInboxItem(
            id="fresh-after-stall",
            recipient_agent_id="worker",
            sender_agent_id="lead",
            task_id="task",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            payload={"message": "New evidence arrived after the stalled attempt."},
            created_at=LEASE_NOW + timedelta(seconds=3),
            updated_at=LEASE_NOW + timedelta(seconds=3),
        )
        _mark_agent_work_due(
            state,
            agent_id="worker",
            now=LEASE_NOW + timedelta(seconds=3),
        )
        return state

    repo_state = repository.update(_add_fresh_inbox_after_stall)
    assert repo_state.agent_runtimes["worker"].no_progress_count == 3
    worker_operations = next(
        row
        for row in runtime_operations_payload(repo_state, now=LEASE_NOW + timedelta(seconds=3))[
            "agents"
        ]
        if row["id"] == "worker"
    )
    assert worker_operations["wake_due"] is True

    _state, fresh_work_leases = runtime.lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW + timedelta(seconds=4),
        max_no_progress_count=3,
        respect_wake_interval=False,
    )
    assert len(fresh_work_leases) == 1
    assert fresh_work_leases[0]["reason"] == "inbox"
    assert fresh_work_leases[0]["dispatch"]["inbox_item_ids"] == ["fresh-after-stall"]
    fresh_dispatch_id = fresh_work_leases[0]["dispatch_id"]
    assert fresh_work_leases[0]["dispatch"]["wake_work_fingerprint"] is not None

    finalized_state, finalized = runtime.finalize_agent_dispatch_from_activity(
        session_id="session-worker",
        dispatch_id=fresh_dispatch_id,
    )
    assert finalized["progress_made"] is False
    assert finalized_state.agent_runtimes["worker"].no_progress_count == 4

    _state, unchanged_replay = runtime.lease_due_agents(
        agent_id="worker",
        now=LEASE_NOW + timedelta(seconds=5),
        max_no_progress_count=3,
        respect_wake_interval=False,
    )
    assert unchanged_replay == []
    worker_operations = next(
        row
        for row in runtime_operations_payload(
            repository.read(),
            now=LEASE_NOW + timedelta(seconds=5),
        )["agents"]
        if row["id"] == "worker"
    )
    assert worker_operations["wake_due"] is False

    def _resolve_stall(state):
        _mark_agent_work_due(
            state,
            agent_id="worker",
            now=LEASE_NOW + timedelta(seconds=6),
        )
        assert state.agent_runtimes["worker"].no_progress_count == 4
        resolve_blocker(
            state,
            blocker=state.blockers[blocker.id],
            actor_agent_id="lead",
            dispatch_id=None,
            note="The owner reassigned and clarified the stalled work.",
            reference_ids=["incident:resolved"],
            now=LEASE_NOW + timedelta(seconds=7),
        )
        return state

    repository.update(_resolve_stall)
    resolved = repository.read()
    assert resolved.agent_runtimes["worker"].no_progress_count == 0
