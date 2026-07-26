from __future__ import annotations

import pytest

from evander.models import utc_now
from evander_team_addon.models import (
    AgentTask,
    Deliverable,
    DeliverableOutcome,
    PersistentAgent,
    Project,
    ReviewRequest,
    TaskAssessmentReadiness,
    TaskCancellation,
    TaskCompletion,
    TaskCompletionKind,
    TaskOutcomeContract,
    TaskResolutionStatus,
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamRecordState,
    TeamState,
    TeamTaskKind,
    TeamTaskState,
)
from evander_team_addon.services.domain.task_assessment import (
    TaskAssessmentBoundaryError,
    TaskAssessmentService,
    assess_task,
    contribution_coverage_for_sources,
    deliverable_source_lineage,
    require_deliverable_acceptance,
    require_deliverable_submission,
    require_release_scope,
    require_task_can_finish,
    task_payload_with_assessment,
)
from evander_team_addon.services.commands.deliverable_helpers import (
    _normalize_deliverable_submission_mapping,
    _validate_command_deliverable_submission_pre_publication,
)
from evander_team_addon.services.commands.types import TeamCommandValidationError
from evander_team_addon.services.domain.deliverables import (
    _accept_deliverable_and_maybe_task,
)
from evander_team_addon.services.domain.release_packages import (
    _build_release_package,
)
from evander_team_addon.services.domain.review import _cascade_review_decision


def _state() -> TeamState:
    # Assessment deliberately tolerates legacy contradictions that TeamState's
    # strict write validator would reject. model_construct gives these focused
    # tests a read-side snapshot without weakening persistence validation.
    return TeamState.model_construct(
        tasks={},
        deliverables={},
        review_requests={},
        blockers={},
        projects={},
    )


def _support_task(
    task_id: str,
    *,
    state: TeamTaskState = TeamTaskState.PENDING,
    parent_task_id: str | None = None,
    dependency_task_ids: list[str] | None = None,
) -> AgentTask:
    return AgentTask(
        id=task_id,
        owner_agent_id=f"owner-{task_id}",
        project_id="project",
        priority=0,
        state=state,
        task_kind=TeamTaskKind.SUPPORT,
        title=task_id,
        description=f"Work for {task_id}",
        created_by_actor="operator:test",
        parent_task_id=parent_task_id,
        dependency_task_ids=dependency_task_ids or [],
    )


def _outcome_task(
    task_id: str,
    *,
    state: TeamTaskState = TeamTaskState.PENDING,
    parent_task_id: str | None = None,
    allow_evidenced_noop: bool = False,
    completion: TaskCompletion | None = None,
) -> AgentTask:
    return AgentTask(
        id=task_id,
        owner_agent_id=f"owner-{task_id}",
        project_id="project",
        priority=0,
        state=state,
        task_kind=TeamTaskKind.OUTCOME,
        outcome_contract=TaskOutcomeContract(
            required_outcome_kinds=(
                [] if allow_evidenced_noop else ["result"]
            ),
            allow_evidenced_noop=allow_evidenced_noop,
        ),
        completion=completion,
        title=task_id,
        description=f"Outcome for {task_id}",
        created_by_actor="operator:test",
        parent_task_id=parent_task_id,
    )


def _delivered(
    state: TeamState,
    *,
    deliverable_id: str,
    task_id: str,
    source_deliverable_ids: list[str] | None = None,
    record_state: TeamRecordState = TeamRecordState.ACCEPTED,
    acceptance_recipient_agent_id: str | None = None,
) -> Deliverable:
    deliverable = Deliverable(
        id=deliverable_id,
        task_id=task_id,
        producer_agent_id=f"owner-{task_id}",
        summary=f"Delivered {task_id}",
        state=record_state,
        outcomes=[
            DeliverableOutcome(
                kind="result",
                reference_ids=[f"ref-{task_id}"],
            )
        ],
        source_deliverable_ids=source_deliverable_ids or [],
        acceptance_recipient_agent_id=acceptance_recipient_agent_id,
    )
    state.deliverables[deliverable.id] = deliverable
    state.tasks[task_id].deliverable_ids.append(deliverable.id)
    return deliverable


def _accepted_outcome(
    state: TeamState,
    task_id: str,
    *,
    parent_task_id: str | None = None,
    source_deliverable_ids: list[str] | None = None,
) -> tuple[AgentTask, Deliverable]:
    task = _outcome_task(
        task_id,
        state=TeamTaskState.ACCEPTED,
        parent_task_id=parent_task_id,
        completion=TaskCompletion(
            kind=TaskCompletionKind.DELIVERED,
            summary=f"Accepted {task_id}",
            outcome_reference_ids=[f"ref-{task_id}"],
        ),
    )
    state.tasks[task.id] = task
    return task, _delivered(
        state,
        deliverable_id=f"deliverable-{task_id}",
        task_id=task.id,
        source_deliverable_ids=source_deliverable_ids,
    )


def test_recursive_assessment_waits_on_unresolved_descendants_and_exposes_blocker() -> None:
    state = _state()
    state.tasks["root"] = _support_task("root")
    state.tasks["child"] = _support_task("child", parent_task_id="root")
    state.tasks["leaf"] = _support_task(
        "leaf",
        state=TeamTaskState.BLOCKED,
        parent_task_id="child",
    )
    state.blockers["blocker-leaf"] = TeamBlocker(
        id="blocker-leaf",
        team_id="team",
        project_id="project",
        task_id="leaf",
        source_kind=TeamBlockerSourceKind.TASK,
        source_id="leaf",
        reason_code="dependency_unavailable",
        owner_agent_id="owner-child",
        summary="Leaf is blocked",
    )

    assessments = TaskAssessmentService(state).assess_all()

    assert assessments["leaf"].resolution_status == TaskResolutionStatus.UNRESOLVED
    assert assessments["leaf"].readiness == TaskAssessmentReadiness.BLOCKED
    assert assessments["child"].readiness == TaskAssessmentReadiness.WAITING_ON_CHILDREN
    assert assessments["root"].readiness == TaskAssessmentReadiness.WAITING_ON_CHILDREN
    assert assessments["root"].unresolved_descendant_ids == ["child", "leaf"]
    assert assessments["root"].active_blocker_ids == ["blocker-leaf"]


def test_task_payload_projects_unique_upstream_blocker_reasons() -> None:
    state = _state()
    state.tasks["root"] = _support_task(
        "root",
        dependency_task_ids=["dependency"],
    )
    state.tasks["child"] = _support_task("child", parent_task_id="root")
    state.tasks["dependency"] = _support_task(
        "dependency",
        state=TeamTaskState.BLOCKED,
    )
    state.blockers["blocker-shared"] = TeamBlocker(
        id="blocker-shared",
        team_id="team",
        project_id="project",
        task_id="dependency",
        affected_task_ids=["child"],
        source_kind=TeamBlockerSourceKind.TASK,
        source_id="dependency",
        reason_code="contract_unavailable",
        owner_agent_id="owner-dependency",
        summary="The signed runtime contract is unavailable.",
        details="The contract approver has not signed the current revision.",
    )
    state.blockers["blocker-child"] = TeamBlocker(
        id="blocker-child",
        team_id="team",
        project_id="project",
        task_id="child",
        source_kind=TeamBlockerSourceKind.TASK,
        source_id="child",
        reason_code="child_environment_failed",
        owner_agent_id="owner-child",
        summary="The child test environment failed.",
    )

    payload = task_payload_with_assessment(state, state.tasks["root"])

    assert payload["active_blocker_ids"] == [
        "blocker-child",
        "blocker-shared",
    ]
    assert payload["inherited_blockers"] == [
        {
            "upstream_task_id": "dependency",
            "relationship": "dependency",
            "blocker_id": "blocker-shared",
            "reason_code": "contract_unavailable",
            "reason": "The signed runtime contract is unavailable.",
            "summary": "The signed runtime contract is unavailable.",
            "details": "The contract approver has not signed the current revision.",
            "source_kind": "task",
            "source_id": "dependency",
            "blocker_task_id": "dependency",
            "owner_agent_id": "owner-dependency",
            "delegate_agent_id": None,
            "route": "owned",
            "escalation_depth": 0,
            "priority": "normal",
            "created_at": payload["inherited_blockers"][0]["created_at"],
            "follow_up_at": None,
            "last_progress_at": None,
        },
        {
            "upstream_task_id": "child",
            "relationship": "descendant",
            "blocker_id": "blocker-child",
            "reason_code": "child_environment_failed",
            "reason": "The child test environment failed.",
            "summary": "The child test environment failed.",
            "details": "",
            "source_kind": "task",
            "source_id": "child",
            "blocker_task_id": "child",
            "owner_agent_id": "owner-child",
            "delegate_agent_id": None,
            "route": "owned",
            "escalation_depth": 0,
            "priority": "normal",
            "created_at": payload["inherited_blockers"][1]["created_at"],
            "follow_up_at": None,
            "last_progress_at": None,
        },
    ]
    assert [
        row["blocker_id"] for row in payload["inherited_blockers"]
    ].count("blocker-shared") == 1


def test_rejected_task_remains_unresolved_for_ancestors_and_dependencies() -> None:
    state = _state()
    state.tasks["parent"] = _support_task("parent")
    state.tasks["rejected"] = _support_task(
        "rejected",
        state=TeamTaskState.REJECTED,
        parent_task_id="parent",
    )
    state.tasks["dependent"] = _support_task(
        "dependent",
        dependency_task_ids=["rejected"],
    )

    rejected = assess_task(state, "rejected")
    parent = assess_task(state, "parent")
    dependent = assess_task(state, "dependent")

    assert rejected.resolution_status == TaskResolutionStatus.UNRESOLVED
    assert rejected.resolution_reason == "rejected_requires_recovery"
    assert rejected.readiness == TaskAssessmentReadiness.BLOCKED
    assert parent.unresolved_descendant_ids == ["rejected"]
    assert parent.readiness == TaskAssessmentReadiness.WAITING_ON_CHILDREN
    assert dependent.unsatisfied_dependency_ids == ["rejected"]
    assert dependent.readiness == TaskAssessmentReadiness.WAITING_ON_DEPENDENCIES


def test_support_outcome_noop_and_cancellation_have_distinct_valid_resolutions() -> None:
    state = _state()
    state.tasks["support"] = _support_task(
        "support",
        state=TeamTaskState.FINISHED,
    )
    _accepted_outcome(state, "delivered")
    noop_completion = TaskCompletion(
        kind=TaskCompletionKind.EVIDENCED_NOOP,
        summary="The durable result already exists.",
        evidence_refs=["evidence:existing"],
    )
    state.tasks["noop"] = _outcome_task(
        "noop",
        state=TeamTaskState.ACCEPTED,
        allow_evidenced_noop=True,
        completion=noop_completion,
    )
    noop = Deliverable(
        id="deliverable-noop",
        task_id="noop",
        producer_agent_id="owner-noop",
        summary="No change was required.",
        state=TeamRecordState.ACCEPTED,
        completion=noop_completion,
    )
    state.deliverables[noop.id] = noop
    state.tasks["noop"].deliverable_ids = [noop.id]
    cancelled_payload = _support_task("cancelled").model_dump()
    cancelled_payload.update(
        state=TeamTaskState.CANCELLED,
        cancellation=TaskCancellation(
            reason_code="obsolete",
            reason="A successor made this task obsolete.",
            actor_kind="operator",
            actor_id="operator:test",
        ),
    )
    state.tasks["cancelled"] = AgentTask.model_validate(cancelled_payload)

    assessments = TaskAssessmentService(state).assess_all()

    assert assessments["support"].resolution_reason == "support_task_finished"
    assert assessments["delivered"].resolution_reason == "outcome_deliverable_accepted"
    assert assessments["noop"].resolution_reason == "outcome_evidenced_noop_accepted"
    assert assessments["cancelled"].resolution_reason == "cancelled_by_authorized_disposition"
    assert all(
        assessments[task_id].resolution_status == TaskResolutionStatus.RESOLVED
        for task_id in ("support", "delivered", "noop", "cancelled")
    )


def test_finish_boundary_assesses_proposed_dependencies() -> None:
    state = _state()
    state.tasks["task"] = _support_task("task")
    state.tasks["dependency"] = _support_task("dependency")

    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_task_can_finish(
            state,
            "task",
            dependency_task_ids=["dependency"],
        )

    assert raised.value.code == "task_boundary_blocked"
    assert raised.value.correction_context["unsatisfied_dependency_ids"] == [
        "dependency"
    ]


@pytest.mark.parametrize(
    ("task_kind", "state_value", "reason_code"),
    [
        (
            TeamTaskKind.OUTCOME,
            TeamTaskState.FINISHED,
            "outcome_task_finished_without_acceptance",
        ),
        (
            TeamTaskKind.SUPPORT,
            TeamTaskState.ACCEPTED,
            "support_task_accepted_instead_of_finished",
        ),
    ],
)
def test_invalid_legacy_terminal_combinations_remain_readable(
    task_kind: TeamTaskKind,
    state_value: TeamTaskState,
    reason_code: str,
) -> None:
    state = _state()
    task = (
        _outcome_task("task")
        if task_kind == TeamTaskKind.OUTCOME
        else _support_task("task")
    )
    # Mutate after model validation to emulate a contradictory historical row.
    task.state = state_value
    state.tasks[task.id] = task

    assessment = assess_task(state, task.id)

    assert assessment.resolution_status == TaskResolutionStatus.INVALID
    assert assessment.readiness == TaskAssessmentReadiness.INVALID
    assert reason_code in assessment.invalid_reason_codes


def test_terminal_parent_with_unresolved_child_is_invalid() -> None:
    state = _state()
    state.tasks["parent"] = _support_task(
        "parent",
        state=TeamTaskState.FINISHED,
    )
    state.tasks["child"] = _support_task("child", parent_task_id="parent")

    assessment = assess_task(state, "parent")

    assert assessment.resolution_status == TaskResolutionStatus.INVALID
    assert assessment.unresolved_descendant_ids == ["child"]
    assert (
        "terminal_task_with_unresolved_descendants"
        in assessment.invalid_reason_codes
    )


def test_active_direct_acceptance_and_formal_review_have_distinct_readiness() -> None:
    state = _state()
    state.tasks["direct"] = _support_task(
        "direct",
        state=TeamTaskState.WAITING_FOR_REVIEW,
    )
    _delivered(
        state,
        deliverable_id="deliverable-direct",
        task_id="direct",
        record_state=TeamRecordState.PENDING,
        acceptance_recipient_agent_id="manager",
    )
    state.tasks["formal"] = _support_task(
        "formal",
        state=TeamTaskState.WAITING_FOR_REVIEW,
    )
    formal = _delivered(
        state,
        deliverable_id="deliverable-formal",
        task_id="formal",
        record_state=TeamRecordState.PENDING,
    )
    formal.outcomes = []
    formal.review_request_ids = ["review-formal"]
    state.review_requests["review-formal"] = ReviewRequest(
        id="review-formal",
        deliverable_id=formal.id,
        reviewer_agent_id="reviewer",
    )

    assessments = TaskAssessmentService(state).assess_all()

    assert assessments["direct"].active_acceptance_ids == ["deliverable-direct"]
    assert (
        assessments["direct"].readiness
        == TaskAssessmentReadiness.WAITING_FOR_ACCEPTANCE
    )
    assert assessments["formal"].active_review_request_ids == ["review-formal"]
    assert (
        assessments["formal"].readiness
        == TaskAssessmentReadiness.WAITING_FOR_REVIEW
    )


def test_direct_child_composition_and_recursive_lineage() -> None:
    state = _state()
    _leaf, leaf_deliverable = _accepted_outcome(state, "leaf", parent_task_id="child")
    _child, child_deliverable = _accepted_outcome(
        state,
        "child",
        parent_task_id="root",
        source_deliverable_ids=[leaf_deliverable.id],
    )
    state.tasks["root"] = _outcome_task("root")

    root = assess_task(state, "root")
    exact = contribution_coverage_for_sources(
        state,
        task_id="root",
        source_deliverable_ids=[child_deliverable.id],
    )
    flattened = contribution_coverage_for_sources(
        state,
        task_id="root",
        source_deliverable_ids=[
            child_deliverable.id,
            leaf_deliverable.id,
        ],
    )
    lineage = deliverable_source_lineage(state, [child_deliverable.id])

    assert root.integration_ready is True
    assert root.contribution_coverage["status"] == "not_submitted"
    assert root.contribution_coverage["required_source_deliverable_ids"] == [
        child_deliverable.id
    ]
    assert exact["complete"] is True
    assert flattened["complete"] is False
    assert flattened["unexpected_source_deliverable_ids"] == [
        leaf_deliverable.id
    ]
    assert lineage["valid"] is True
    assert lineage["graph"] == {
        child_deliverable.id: [leaf_deliverable.id],
        leaf_deliverable.id: [],
    }
    assert lineage["source_deliverable_ids"] == [leaf_deliverable.id]


def test_submission_boundary_returns_exact_missing_contribution_ids() -> None:
    state = _state()
    _child, child_deliverable = _accepted_outcome(
        state,
        "child",
        parent_task_id="root",
    )
    state.tasks["root"] = _outcome_task("root")

    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_deliverable_submission(
            state,
            task_id="root",
            source_deliverable_ids=[],
        )

    assert raised.value.code == "contribution_coverage_incomplete"
    assert raised.value.correction_context["contribution_coverage"][
        "missing_source_deliverable_ids"
    ] == [child_deliverable.id]


@pytest.mark.parametrize(
    "task",
    [
        _support_task("finished", state=TeamTaskState.FINISHED),
        AgentTask.model_validate(
            {
                **_support_task("cancelled").model_dump(),
                "state": TeamTaskState.CANCELLED,
                "cancellation": TaskCancellation(
                    reason_code="obsolete",
                    reason="A successor made this task obsolete.",
                    actor_kind="operator",
                    actor_id="operator:test",
                ),
            }
        ),
    ],
)
def test_submission_boundary_rejects_resolved_tasks(task: AgentTask) -> None:
    state = _state()
    state.tasks[task.id] = task

    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_deliverable_submission(
            state,
            task_id=task.id,
            source_deliverable_ids=[],
        )

    assert raised.value.code == "task_already_resolved"
    assert raised.value.correction_context["resolution_status"] == "resolved"


def test_acceptance_boundary_rejects_unresolved_descendants() -> None:
    state = _state()
    state.tasks["parent"] = _outcome_task(
        "parent",
        state=TeamTaskState.WAITING_FOR_REVIEW,
    )
    state.tasks["child"] = _support_task("child", parent_task_id="parent")
    parent_deliverable = _delivered(
        state,
        deliverable_id="deliverable-parent",
        task_id="parent",
        record_state=TeamRecordState.PENDING,
        acceptance_recipient_agent_id="manager",
    )

    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_deliverable_acceptance(state, parent_deliverable.id)

    assert raised.value.code == "task_boundary_blocked"
    assert raised.value.correction_context["unresolved_descendant_ids"] == [
        "child"
    ]


def test_release_scope_accepts_top_level_aggregate_and_rejects_flattened_source() -> None:
    state = _state()
    _child, child_deliverable = _accepted_outcome(
        state,
        "child",
        parent_task_id="root",
    )
    _root, root_deliverable = _accepted_outcome(
        state,
        "root",
        source_deliverable_ids=[child_deliverable.id],
    )

    result = require_release_scope(
        state,
        project_id="project",
        deliverable_ids=[root_deliverable.id],
    )

    assert result["valid"] is True
    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_release_scope(
            state,
            project_id="project",
            deliverable_ids=[
                root_deliverable.id,
                child_deliverable.id,
            ],
        )
    assert raised.value.code == "release_scope_invalid"
    context = raised.value.correction_context
    assert context["redundant_source_deliverable_ids"] == [
        child_deliverable.id
    ]
    assert "release_scope_not_top_level" in context["errors"][
        child_deliverable.id
    ]


def test_mixed_hierarchy_dependency_cycle_is_invalid_without_recursion_failure() -> None:
    state = _state()
    state.tasks["parent"] = _support_task("parent")
    state.tasks["child"] = _support_task(
        "child",
        parent_task_id="parent",
        dependency_task_ids=["parent"],
    )

    assessments = TaskAssessmentService(state).assess_all()

    assert assessments["parent"].resolution_status == TaskResolutionStatus.INVALID
    assert assessments["child"].resolution_status == TaskResolutionStatus.INVALID
    assert "hierarchy_dependency_cycle" in assessments["parent"].invalid_reason_codes
    assert "hierarchy_dependency_cycle" in assessments["child"].invalid_reason_codes


def test_finish_boundary_allows_resolved_children_but_forbids_outcome_direct_finish() -> None:
    state = _state()
    state.tasks["parent"] = _support_task("parent")
    state.tasks["child"] = _support_task(
        "child",
        state=TeamTaskState.FINISHED,
        parent_task_id="parent",
    )
    state.tasks["outcome"] = _outcome_task("outcome")

    assert require_task_can_finish(state, "parent").integration_ready is True
    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_task_can_finish(state, "outcome")
    assert raised.value.code == "outcome_task_direct_finish_forbidden"


def test_task_payload_exposes_invalid_legacy_assessment_without_failing_read() -> None:
    state = _state()
    task = _outcome_task("legacy")
    task.state = TeamTaskState.FINISHED
    state.tasks[task.id] = task

    payload = task_payload_with_assessment(state, task)

    assert payload["state"] == "finished"
    assert payload["resolution_status"] == "invalid"
    assert payload["readiness"] == "invalid"
    assert payload["assessment"]["invalid_reason_codes"] == [
        "outcome_task_finished_without_acceptance"
    ]


def test_submission_normalization_and_acceptance_preserve_evidenced_noop() -> None:
    state = _state()
    task = _outcome_task(
        "noop",
        state=TeamTaskState.WAITING_FOR_REVIEW,
        allow_evidenced_noop=True,
    )
    state.tasks[task.id] = task
    normalized = _normalize_deliverable_submission_mapping(
        {
            "task_id": task.id,
            "summary": "No mutation was needed.",
            "completion": {
                "kind": "evidenced_noop",
                "summary": "The durable record already exists.",
                "evidence_refs": ["duplicate-check:123"],
            },
            "source_deliverable_ids": [],
        },
        field_name="deliverable",
    )
    deliverable = Deliverable(
        id="deliverable-noop",
        task_id=task.id,
        producer_agent_id=task.owner_agent_id,
        summary=normalized["summary"],
        state=TeamRecordState.PENDING,
        acceptance_recipient_agent_id="manager",
        completion=normalized["completion"],
        source_deliverable_ids=normalized["source_deliverable_ids"],
    )
    state.deliverables[deliverable.id] = deliverable
    task.deliverable_ids = [deliverable.id]

    require_deliverable_acceptance(state, deliverable.id)
    accepted_task = _accept_deliverable_and_maybe_task(
        state,
        deliverable=deliverable,
        task=task,
        now=utc_now(),
    )

    assert accepted_task is True
    assert deliverable.state == TeamRecordState.ACCEPTED
    assert task.state == TeamTaskState.ACCEPTED
    assert task.completion == deliverable.completion
    assert assess_task(state, task.id).resolution_reason == (
        "outcome_evidenced_noop_accepted"
    )


def test_support_deliverable_acceptance_finishes_instead_of_accepting_task() -> None:
    state = _state()
    task = _support_task("support", state=TeamTaskState.WAITING_FOR_REVIEW)
    state.tasks[task.id] = task
    deliverable = Deliverable(
        id="deliverable-support",
        task_id=task.id,
        producer_agent_id=task.owner_agent_id,
        summary="Coordination output is ready.",
        state=TeamRecordState.PENDING,
        acceptance_recipient_agent_id="manager",
    )
    state.deliverables[deliverable.id] = deliverable
    task.deliverable_ids = [deliverable.id]

    require_deliverable_acceptance(state, deliverable.id)
    _accept_deliverable_and_maybe_task(
        state,
        deliverable=deliverable,
        task=task,
        now=utc_now(),
    )

    assert task.state == TeamTaskState.FINISHED
    assert assess_task(state, task.id).resolution_status == TaskResolutionStatus.RESOLVED


def test_submission_command_boundary_reports_unresolved_descendants_before_side_effects() -> None:
    state = _state()
    parent = _outcome_task("parent")
    state.tasks[parent.id] = parent
    state.tasks["child"] = _support_task("child", parent_task_id=parent.id)
    agent = PersistentAgent.model_construct(
        id=parent.owner_agent_id,
        team_id="team",
    )

    with pytest.raises(TeamCommandValidationError) as raised:
        _validate_command_deliverable_submission_pre_publication(
            state,
            agent=agent,
            task=parent,
            deliverable={
                "summary": "Premature aggregate",
                "source_deliverable_ids": [],
                "git_refs": {},
                "outcomes": [
                    {
                        "kind": "result",
                        "reference_ids": ["ref-parent"],
                    }
                ],
            },
            deliverable_id="deliverable-parent",
            dispatch_id=None,
        )

    assert raised.value.code == "task_boundary_blocked"
    assert raised.value.correction_context["unresolved_descendant_ids"] == [
        "child"
    ]
    assert state.deliverables == {}


def test_formal_review_cascade_refuses_acceptance_with_unresolved_descendants() -> None:
    state = _state()
    parent = _outcome_task(
        "parent",
        state=TeamTaskState.WAITING_FOR_REVIEW,
    )
    state.tasks[parent.id] = parent
    state.tasks["child"] = _support_task("child", parent_task_id=parent.id)
    deliverable = _delivered(
        state,
        deliverable_id="deliverable-parent",
        task_id=parent.id,
        record_state=TeamRecordState.PENDING,
    )
    review = ReviewRequest(
        id="review-parent",
        deliverable_id=deliverable.id,
        reviewer_agent_id="reviewer",
    )
    state.review_requests[review.id] = review
    deliverable.review_request_ids = [review.id]

    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        _cascade_review_decision(
            state,
            review=review,
            decision_state=TeamRecordState.ACCEPTED,
            findings=[],
            projects_to_recalc=set(),
            result={"errors": []},
        )

    assert raised.value.correction_context["unresolved_descendant_ids"] == [
        "child"
    ]
    assert review.state == TeamRecordState.PENDING
    assert deliverable.state == TeamRecordState.PENDING


def test_release_construction_refuses_non_top_level_or_unresolved_scope() -> None:
    state = _state()
    state.tasks["root"] = _support_task("root")
    state.tasks["child"] = _support_task(
        "child",
        state=TeamTaskState.FINISHED,
        parent_task_id="root",
    )
    child_deliverable = Deliverable(
        id="deliverable-child",
        task_id="child",
        producer_agent_id="owner-child",
        summary="Child result",
        state=TeamRecordState.ACCEPTED,
        artifact_refs=["artifact:child"],
    )
    state.deliverables[child_deliverable.id] = child_deliverable
    state.tasks["child"].deliverable_ids = [child_deliverable.id]
    result: dict[str, object] = {"errors": []}

    created_id = _build_release_package(
        state,
        project=Project.model_construct(id="project", team_id="team"),
        request={"deliverable_ids": [child_deliverable.id]},
        result=result,
        projects_to_recalc=set(),
        actor_agent_id="owner-child",
        team_id="team",
    )

    assert created_id is None
    assert result["task_assessment_error"]["code"] == "release_scope_invalid"
    assert result["task_assessment_error"]["errors"][child_deliverable.id] == [
        "release_scope_not_top_level"
    ]
