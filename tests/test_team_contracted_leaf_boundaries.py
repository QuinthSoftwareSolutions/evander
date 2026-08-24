from __future__ import annotations

import pytest

from evander_team_addon.models import (
    AgentTask,
    Deliverable,
    PersistentAgent,
    TaskCancellation,
    TaskDeliverableContract,
    TeamRecordState,
    TeamState,
    TeamTaskKind,
    TeamTaskState,
)
from evander_team_addon.services.domain.case_task import (
    _TaskCreationPlan,
    _task_creation_batch_graph_errors,
    _task_creation_plan_graph_error,
    _task_deliverable_contract_scope_error,
    _task_graph_edit_error,
)
from evander_team_addon.services.domain.submitted_integration_recovery import (
    plan_submitted_integration_boundary_recovery,
)


def _task(
    task_id: str,
    *,
    parent_task_id: str | None = None,
    contracted: bool = False,
) -> AgentTask:
    return AgentTask(
        id=task_id,
        owner_agent_id="owner",
        project_id="project",
        priority=0,
        state=TeamTaskState.PENDING,
        task_kind=TeamTaskKind.SUPPORT,
        title=task_id,
        description=task_id,
        created_by_actor="operator:test",
        parent_task_id=parent_task_id,
        deliverable_contract=(
            TaskDeliverableContract(require_git_provenance=True) if contracted else None
        ),
    )


def _plan(
    task_id: str,
    *,
    parent_task_id: str | None = None,
    contracted: bool = False,
) -> _TaskCreationPlan:
    return _TaskCreationPlan(
        task_id=task_id,
        owner_id="owner",
        project_id="project",
        case_id=None,
        task_state=TeamTaskState.PENDING,
        parent_task_id=parent_task_id,
        originating_task_id=None,
        git_base_branch=None,
        dependency_task_ids=[],
        priority=0,
        title=task_id,
        description=task_id,
        task_kind=TeamTaskKind.SUPPORT,
        outcome_contract=None,
        integration_boundary=False,
        deliverable_contract=(
            TaskDeliverableContract(require_git_provenance=True) if contracted else None
        ),
    )


def test_task_creation_plan_rejects_child_under_existing_contracted_leaf() -> None:
    parent = _task("contracted-parent", contracted=True)
    child = _plan("child", parent_task_id=parent.id)
    state = TeamState.model_construct(
        tasks={parent.id: parent},
        deliverables={},
        review_requests={},
        blockers={},
        projects={},
    )
    actor = PersistentAgent(
        id="owner",
        team_id="team",
        role_id="role",
        session_id="session-owner",
    )

    errors = _task_creation_batch_graph_errors(state, [child], actor=actor)
    error = errors[child.task_id]

    assert error is not None
    assert "contracted leaf and cannot own child tasks" in error
    assert "originating_task_id" in error


def test_batch_creation_plan_rejects_child_under_planned_contracted_leaf() -> None:
    parent = _plan("contracted-parent", contracted=True)
    child = _plan("child", parent_task_id=parent.task_id)
    plans = [parent, child]

    error = _task_creation_plan_graph_error(
        child,
        task_projects={plan.task_id: plan.project_id for plan in plans},
        parent_map={plan.task_id: plan.parent_task_id for plan in plans},
        integration_boundary_map={plan.task_id: plan.integration_boundary for plan in plans},
        deliverable_contract_map={plan.task_id: plan.deliverable_contract for plan in plans},
        dependency_map={plan.task_id: list(plan.dependency_task_ids) for plan in plans},
    )

    assert error is not None
    assert "contracted leaf and cannot own child tasks" in error


def test_graph_edit_rejects_new_parent_edge_but_allows_historical_edge() -> None:
    parent = _task("contracted-parent", contracted=True)
    existing_child = _task("existing-child", parent_task_id=parent.id)
    reparented = _task("reparented")
    state = TeamState.model_construct(
        tasks={
            parent.id: parent,
            existing_child.id: existing_child,
            reparented.id: reparented,
        },
        deliverables={},
        review_requests={},
        blockers={},
        projects={},
    )

    unchanged_error = _task_graph_edit_error(
        state,
        task_id=existing_child.id,
        project_id=existing_child.project_id,
        parent_task_id=parent.id,
        dependency_task_ids=[],
    )
    reparent_error = _task_graph_edit_error(
        state,
        task_id=reparented.id,
        project_id=reparented.project_id,
        parent_task_id=parent.id,
        dependency_task_ids=[],
    )

    assert unchanged_error is None
    assert reparent_error is not None
    assert "contracted leaf and cannot own child tasks" in reparent_error


@pytest.mark.parametrize(
    "dependency_state",
    [TeamTaskState.CANCELLED, TeamTaskState.REJECTED],
)
def test_graph_edit_rejects_new_dead_end_dependency_but_preserves_historical_edge(
    dependency_state: TeamTaskState,
) -> None:
    dead_end = _task("dead-end")
    dead_end.state = dependency_state
    historical_dependent = _task("historical-dependent")
    historical_dependent.dependency_task_ids = [dead_end.id]
    new_dependent = _task("new-dependent")
    state = TeamState.model_construct(
        tasks={
            dead_end.id: dead_end,
            historical_dependent.id: historical_dependent,
            new_dependent.id: new_dependent,
        },
        deliverables={},
        review_requests={},
        blockers={},
        projects={},
    )

    historical_error = _task_graph_edit_error(
        state,
        task_id=historical_dependent.id,
        project_id=historical_dependent.project_id,
        parent_task_id=historical_dependent.parent_task_id,
        dependency_task_ids=[dead_end.id],
    )
    new_edge_error = _task_graph_edit_error(
        state,
        task_id=new_dependent.id,
        project_id=new_dependent.project_id,
        parent_task_id=new_dependent.parent_task_id,
        dependency_task_ids=[dead_end.id],
    )

    assert historical_error is None
    assert new_edge_error == (
        f"task cannot add dependency dead-end because it is {dependency_state.value}"
    )


def test_task_with_children_cannot_become_contracted_leaf() -> None:
    parent = _task("parent")
    child = _task("child", parent_task_id=parent.id)
    state = TeamState.model_construct(
        tasks={parent.id: parent, child.id: child},
        deliverables={},
        review_requests={},
        blockers={},
        projects={},
    )

    error = _task_deliverable_contract_scope_error(
        state,
        task_id=parent.id,
        project_id=parent.project_id,
        dependency_task_ids=[],
        integration_boundary=False,
        deliverable_contract=TaskDeliverableContract(require_git_provenance=True),
    )

    assert error is not None
    assert "has direct children and cannot become a contracted leaf" in error


def test_submitted_boundary_recovery_turns_child_contribution_into_dependency() -> None:
    integration = _task("integration")
    integration.integration_boundary = True
    integration.deliverable_contract = TaskDeliverableContract(
        require_git_provenance=False,
        required_contribution_task_ids=["predecessor"],
    )
    integration.deliverable_ids = ["submitted"]
    integration.state = TeamTaskState.WAITING_FOR_REVIEW
    predecessor = _task("predecessor", parent_task_id=integration.id)
    predecessor.state = TeamTaskState.CANCELLED
    predecessor.cancellation = TaskCancellation(
        reason_code="replaced",
        reason="Use the recorded successor contribution.",
        actor_kind="operator",
        actor_id="operator:test",
        successor_task_id="successor",
    )
    successor = _task("successor")
    successor.state = TeamTaskState.FINISHED
    submitted = Deliverable(
        id="submitted",
        task_id=integration.id,
        producer_agent_id="owner",
        summary="Immutable historical submission.",
        state=TeamRecordState.REJECTED,
    )
    state = TeamState.model_construct(
        tasks={
            integration.id: integration,
            predecessor.id: predecessor,
            successor.id: successor,
        },
        deliverables={submitted.id: submitted},
        review_requests={},
        blockers={},
        projects={},
        git_work_contexts={},
    )

    plan = plan_submitted_integration_boundary_recovery(
        state,
        predecessor_task_id=predecessor.id,
        successor_task_id=successor.id,
        integration_task_id=integration.id,
        replacement_task_id="replacement",
        expected_dependency_task_ids=(),
        expected_required_contribution_task_ids=(predecessor.id,),
        reason="Rebuild the immutable boundary from its recorded successor.",
        git_base_commit_sha=None,
        git_base_branch=None,
        actor_kind="operator",
    )

    assert plan.replacement_required_contribution_task_ids == (successor.id,)
    assert plan.replacement_dependency_task_ids == (successor.id,)
    assert integration.dependency_task_ids == []
    assert predecessor.parent_task_id == integration.id
