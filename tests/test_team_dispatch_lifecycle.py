from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evander_team_addon.models import (
    AgentInboxItem,
    Deliverable,
    ProjectGitConfig,
    ProjectGoal,
    ReleasePackage,
    ReviewBlockKind,
    ReviewRequest,
    RolePermission,
    TeamBlockerEventType,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamDispatch,
    TeamDispatchStatus,
    TeamInboxItemType,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import (
    TeamChatService,
    project_scoped_team_chat_authorization,
)
from evander_team_addon.services.commands import (
    TeamBlockerCommandService,
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
    assert (
        state.dispatches[f"dispatch-{status.value}"].session_id
        == "session-worker"
    )


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

    _state, out_of_scope = TeamTaskUpdateCommandService(
        repository
    ).update_task_command(
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
    assert "must belong to project project and team team" in (
        mismatched_origin["errors"][0]["message"]
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
        blocker
        for blocker in state.blockers.values()
        if blocker.source_id == "child-task"
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

    state, decided = TeamDeliverableSubmissionCommandService(
        repository
    ).decide_deliverable_command(
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
    assert assess_task(state, "task").active_review_request_ids == [
        "blocked-review"
    ]

    state, cancelled = TeamTaskUpdateCommandService(
        repository
    ).cancel_task_command(
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
        blocker
        for blocker in state.blockers.values()
        if blocker.source_id == "blocked-review"
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

    _state, archived_memory = TeamMemoryCommandService(
        repository
    ).archive_memory_command(
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
        if event.blocker_id == blocker.id
        and event.event_type == TeamBlockerEventType.REPORTED
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
        if event.blocker_id == blocker.id
        and event.event_type == TeamBlockerEventType.RESOLVED
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
        "dispatch scope became stale during preparation: "
        "task task is now assigned to lead"
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
        item.recipient_agent_id == "lead"
        and item.payload.get("blocker_id") == blocker.id
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
    assert len(
        [
            item
            for item in replay.blockers.values()
            if item.source_kind
            == TeamBlockerSourceKind.AGENT_EXECUTION_STALL
            and item.source_id == "worker"
        ]
    ) == 1
    assert len(
        [
            event
            for event in replay.blocker_events.values()
            if event.blocker_id == blocker.id
            and event.event_type == TeamBlockerEventType.PROGRESS
            and event.metadata.get("kind") == "execution_stall_exhausted"
        ]
    ) == 1

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

    def _resolve_stall(state):
        _mark_agent_work_due(
            state,
            agent_id="worker",
            now=LEASE_NOW + timedelta(seconds=3),
        )
        assert state.agent_runtimes["worker"].no_progress_count == 3
        resolve_blocker(
            state,
            blocker=state.blockers[blocker.id],
            actor_agent_id="lead",
            dispatch_id=None,
            note="The owner reassigned and clarified the stalled work.",
            reference_ids=["incident:resolved"],
            now=LEASE_NOW + timedelta(seconds=4),
        )
        return state

    repository.update(_resolve_stall)
    resolved = repository.read()
    assert resolved.agent_runtimes["worker"].no_progress_count == 0
