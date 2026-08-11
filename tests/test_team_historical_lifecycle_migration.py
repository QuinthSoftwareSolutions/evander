from __future__ import annotations

import pytest

from evander_team_addon.models import (
    AgentTask,
    Deliverable,
    PersistentAgent,
    Project,
    Team,
    TeamBlockerEventType,
    TeamBlockerState,
    TeamRecordState,
    TeamRole,
    TeamState,
    TeamTaskKind,
    TeamTaskState,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.historical_lifecycle_migration import (
    HistoricalLifecycleMigrationError,
)
from evander_team_addon.services.domain.task_assessment import assess_tasks


def _state_with_repairable_legacy_lifecycle() -> TeamState:
    state = TeamState()
    state.teams["team"] = Team(
        id="team",
        name="Team",
        manager_agent_id="manager",
        project_ids=["project"],
    )
    state.projects["project"] = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
    )
    state.roles["role"] = TeamRole(
        id="role",
        team_id="team",
        name="Role",
    )
    for agent_id, reports_to in (("manager", None), ("lead", "manager"), ("worker", "lead")):
        state.agents[agent_id] = PersistentAgent(
            id=agent_id,
            team_id="team",
            role_id="role",
            session_id=f"session-{agent_id}",
            reports_to_agent_id=reports_to,
        )
    state.tasks["parent"] = AgentTask(
        id="parent",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        state=TeamTaskState.FINISHED,
        task_kind=TeamTaskKind.SUPPORT,
        title="Terminal parent",
        description="A parent that will resolve after its historical child is repaired.",
        created_by_actor="operator:test",
    )
    state.tasks["accepted-support"] = AgentTask(
        id="accepted-support",
        owner_agent_id="worker",
        project_id="project",
        parent_task_id="parent",
        priority=1,
        state=TeamTaskState.ACCEPTED,
        task_kind=TeamTaskKind.SUPPORT,
        title="Accepted support task",
        description="Historical support task stored in the wrong terminal state.",
        deliverable_ids=["accepted-deliverable"],
        created_by_agent_id="lead",
    )
    state.deliverables["accepted-deliverable"] = Deliverable(
        id="accepted-deliverable",
        task_id="accepted-support",
        producer_agent_id="worker",
        summary="Accepted evidence that must remain accepted.",
        state=TeamRecordState.ACCEPTED,
    )
    state.tasks["orphaned-deliverable"] = AgentTask(
        id="orphaned-deliverable",
        owner_agent_id="worker",
        project_id="project",
        priority=2,
        state=TeamTaskState.FINISHED,
        task_kind=TeamTaskKind.SUPPORT,
        title="Terminal task with orphaned deliverable",
        description="Historical pending deliverable has no acceptance route.",
        deliverable_ids=["pending-deliverable"],
        created_by_agent_id="lead",
    )
    state.deliverables["pending-deliverable"] = Deliverable(
        id="pending-deliverable",
        task_id="orphaned-deliverable",
        producer_agent_id="worker",
        summary="Evidence retained even though the historical handoff is orphaned.",
        state=TeamRecordState.PENDING,
    )
    return state


def test_historical_lifecycle_migration_repairs_exact_records_and_audits_each_task(
    tmp_path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    repository.update(lambda _state: _state_with_repairable_legacy_lifecycle())
    service = TeamAdminService(repository)

    planned_state, plan = service.plan_historical_lifecycle_migration(project_id="project")

    assert planned_state.revision > 0
    assert plan.task_ids == ("accepted-support", "orphaned-deliverable", "parent")
    assert plan.task_repairs[0].action_kinds == ("finish_accepted_support_task",)
    assert plan.task_repairs[1].action_kinds == ("retire_orphaned_pending_deliverable",)
    assert plan.task_repairs[2].action_kinds == ("reassess_terminal_parent_after_child_repair",)

    state, result = service.migrate_historical_task_lifecycle(
        project_id="project",
        actor="operator:test",
        reason="Operator-authorized historical lifecycle migration.",
        reference_ids=["directive-123"],
        expected_revision=planned_state.revision,
    )

    assert result["applied"] is True
    assert len(result["audit_blocker_ids"]) == 3
    assert state.projects["project"].status.value == "completed"
    assert state.tasks["accepted-support"].state == TeamTaskState.FINISHED
    assert state.deliverables["accepted-deliverable"].state == TeamRecordState.ACCEPTED
    assert state.deliverables["pending-deliverable"].state == TeamRecordState.REJECTED
    assessments = assess_tasks(state)
    assert all(
        assessments[task_id].resolution_status.value == "resolved" for task_id in plan.task_ids
    )

    audit_blockers = [
        blocker
        for blocker in state.blockers.values()
        if blocker.reason_code == "historical_lifecycle_migration"
    ]
    assert len(audit_blockers) == 3
    assert all(blocker.state == TeamBlockerState.RESOLVED for blocker in audit_blockers)
    for blocker in audit_blockers:
        events = [
            event for event in state.blocker_events.values() if event.blocker_id == blocker.id
        ]
        assert [event.event_type for event in events] == [
            TeamBlockerEventType.REPORTED,
            TeamBlockerEventType.RESOLVED,
        ]
        assert "directive-123" in events[-1].reference_ids
        assert events[-1].metadata["invalid_reason_codes_after"] == []

    reread, second_plan = service.plan_historical_lifecycle_migration(project_id="project")
    second_state, second_result = service.migrate_historical_task_lifecycle(
        project_id="project",
        actor="operator:test",
        reason="Operator-authorized historical lifecycle migration.",
        expected_revision=reread.revision,
    )
    assert second_plan.task_repairs == ()
    assert second_result["applied"] is False
    assert second_state.revision == reread.revision


def test_historical_lifecycle_migration_fails_closed_for_unrecognized_invalid_state(
    tmp_path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    state = _state_with_repairable_legacy_lifecycle()
    state.tasks["unrelated-invalid-task"] = AgentTask(
        id="unrelated-invalid-task",
        owner_agent_id="worker",
        project_id="project",
        parent_task_id="missing-parent",
        priority=3,
        state=TeamTaskState.FINISHED,
        task_kind=TeamTaskKind.SUPPORT,
        title="Unexpected invalid task",
        description="The historical migration must not attempt this repair.",
        created_by_agent_id="lead",
    )
    repository.update(lambda _state: state)

    with pytest.raises(HistoricalLifecycleMigrationError, match="parent_task_missing"):
        TeamAdminService(repository).plan_historical_lifecycle_migration(project_id="project")
