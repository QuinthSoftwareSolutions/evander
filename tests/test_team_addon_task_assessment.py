from __future__ import annotations

from typing import Any

import pytest

from evander.models import utc_now
from evander_team_addon.models import (
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    DeliverableOutcome,
    DeliverableVerification,
    DependencyCompositionManifest,
    DependencyGitSource,
    EphemeralAssignment,
    EphemeralAssignmentState,
    GitPromotionRecord,
    GitWorkContext,
    GitWorkContextState,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ReviewRequest,
    TaskAssessmentReadiness,
    TaskCancellation,
    TaskCompletion,
    TaskCompletionKind,
    TaskDeliverableContract,
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
    _superseded_deliverable_for_rework,
)
from evander_team_addon.services.domain.ephemeral import (
    _apply_ephemeral_acceptance,
)
from evander_team_addon.services.domain.git_promotion import (
    _attach_promotion_success,
)
from evander_team_addon.services.domain.git_work_contexts import (
    bind_deliverable_git_refs_to_work_context,
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
            required_outcome_kinds=([] if allow_evidenced_noop else ["result"]),
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


def test_accepted_deliverable_rework_unresolves_task_and_terminal_ancestors() -> None:
    state = _state()
    state.tasks["parent"] = _support_task(
        "parent",
        state=TeamTaskState.FINISHED,
    )
    state.tasks["child"] = _support_task(
        "child",
        state=TeamTaskState.IN_PROGRESS,
        parent_task_id="parent",
    )
    deliverable = _delivered(
        state,
        deliverable_id="deliverable-child",
        task_id="child",
    )
    deliverable.rework_requested_at = utc_now()
    deliverable.rework_requested_by_agent_id = "owner-child"
    deliverable.rework_reason = "Accepted evidence later proved invalid."

    assessments = TaskAssessmentService(state).assess_all()

    assert assessments["child"].resolution_status == TaskResolutionStatus.UNRESOLVED
    assert assessments["child"].resolution_reason == "accepted_deliverable_requires_replacement"
    assert assessments["parent"].resolution_status != TaskResolutionStatus.RESOLVED
    assert assessments["parent"].unresolved_descendant_ids == ["child"]


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
    assert [row["blocker_id"] for row in payload["inherited_blockers"]].count("blocker-shared") == 1


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


def test_cancelled_legacy_aggregate_ignores_stale_contribution_coverage() -> None:
    state = _state()
    cancelled_payload = _support_task("aggregate").model_dump()
    cancelled_payload.update(
        state=TeamTaskState.CANCELLED,
        cancellation=TaskCancellation(
            reason_code="operator_release_abandoned",
            reason="The rejected release no longer needs a correction.",
            actor_kind="operator",
            actor_id="operator:test",
        ),
    )
    state.tasks["aggregate"] = AgentTask.model_validate(cancelled_payload)
    state.tasks["older-child"] = _support_task(
        "older-child",
        state=TeamTaskState.FINISHED,
        parent_task_id="aggregate",
    )
    state.tasks["newer-child"] = _support_task(
        "newer-child",
        state=TeamTaskState.FINISHED,
        parent_task_id="aggregate",
    )
    older_deliverable = _delivered(
        state,
        deliverable_id="deliverable-older-child",
        task_id="older-child",
    )
    _delivered(
        state,
        deliverable_id="deliverable-newer-child",
        task_id="newer-child",
    )
    aggregate_deliverable = _delivered(
        state,
        deliverable_id="deliverable-aggregate",
        task_id="aggregate",
        source_deliverable_ids=[older_deliverable.id],
    )
    aggregate_deliverable.rework_requested_at = utc_now()
    aggregate_deliverable.rework_requested_by_agent_id = "reviewer"
    aggregate_deliverable.rework_reason = "A later finding requested correction."

    assessment = assess_task(state, "aggregate")

    assert assessment.contribution_coverage["status"] == "incomplete"
    assert assessment.invalid_reason_codes == []
    assert assessment.resolution_status == TaskResolutionStatus.RESOLVED
    assert assessment.resolution_reason == "cancelled_by_authorized_disposition"


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
    assert raised.value.correction_context["unsatisfied_dependency_ids"] == ["dependency"]


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
    task = _outcome_task("task") if task_kind == TeamTaskKind.OUTCOME else _support_task("task")
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
    assert "terminal_task_with_unresolved_descendants" in assessment.invalid_reason_codes


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
    assert assessments["direct"].readiness == TaskAssessmentReadiness.WAITING_FOR_ACCEPTANCE
    assert assessments["formal"].active_review_request_ids == ["review-formal"]
    assert assessments["formal"].readiness == TaskAssessmentReadiness.WAITING_FOR_REVIEW


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
    assert root.contribution_coverage["required_source_deliverable_ids"] == [child_deliverable.id]
    assert exact["complete"] is True
    assert flattened["complete"] is False
    assert flattened["unexpected_source_deliverable_ids"] == [leaf_deliverable.id]
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
    assert raised.value.correction_context["unresolved_descendant_ids"] == ["child"]


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
    assert context["redundant_source_deliverable_ids"] == [child_deliverable.id]
    assert "release_scope_not_top_level" in context["errors"][child_deliverable.id]


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


def test_integration_boundary_forbids_direct_finish_and_uses_explicit_dependency_sources() -> None:
    state = _state()
    dependency = _support_task(
        "dependency",
        state=TeamTaskState.FINISHED,
    )
    unrelated_child = _support_task(
        "unrelated-child",
        state=TeamTaskState.FINISHED,
        parent_task_id="integration",
    )
    integration = AgentTask(
        **{
            **_support_task(
                "integration",
                dependency_task_ids=[dependency.id],
            ).model_dump(),
            "integration_boundary": True,
            "deliverable_contract": TaskDeliverableContract(
                required_contribution_task_ids=[dependency.id],
            ),
        }
    )
    state.tasks = {
        dependency.id: dependency,
        unrelated_child.id: unrelated_child,
        integration.id: integration,
    }
    dependency_deliverable = _delivered(
        state,
        deliverable_id="deliverable-dependency",
        task_id=dependency.id,
    )
    unrelated_deliverable = _delivered(
        state,
        deliverable_id="deliverable-unrelated",
        task_id=unrelated_child.id,
    )

    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_task_can_finish(state, integration.id)

    assert raised.value.code == "integration_boundary_direct_finish_forbidden"
    exact = contribution_coverage_for_sources(
        state,
        task_id=integration.id,
        source_deliverable_ids=[dependency_deliverable.id],
    )
    incidental_child = contribution_coverage_for_sources(
        state,
        task_id=integration.id,
        source_deliverable_ids=[unrelated_deliverable.id],
    )
    assert exact["complete"] is True
    assert exact["required_contribution_task_ids"] == [dependency.id]
    assert incidental_child["complete"] is False
    assert incidental_child["unexpected_source_deliverable_ids"] == [unrelated_deliverable.id]


def test_contracted_leaf_forbids_direct_finish_without_becoming_integration_boundary() -> None:
    state = _state()
    task = AgentTask(
        **{
            **_support_task("contracted-leaf").model_dump(),
            "deliverable_contract": TaskDeliverableContract(
                require_git_provenance=True,
            ),
        }
    )
    state.tasks[task.id] = task

    assert task.integration_boundary is False
    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_task_can_finish(state, task.id)

    assert raised.value.code == "deliverable_contract_direct_finish_forbidden"
    assert raised.value.correction_context["supported_tools"] == [
        "team_submit_deliverable",
        "team_submit_deliverable_for_review",
    ]


def test_contracted_leaf_does_not_aggregate_incidental_child_deliverables() -> None:
    state = _state()
    contracted_leaf = AgentTask(
        **{
            **_support_task("contracted-leaf").model_dump(),
            "deliverable_contract": TaskDeliverableContract(
                require_git_provenance=True,
            ),
        }
    )
    child = _support_task(
        "incidental-child",
        state=TeamTaskState.FINISHED,
        parent_task_id=contracted_leaf.id,
    )
    state.tasks = {
        contracted_leaf.id: contracted_leaf,
        child.id: child,
    }
    child_deliverable = _delivered(
        state,
        deliverable_id="deliverable-incidental-child",
        task_id=child.id,
    )

    direct = contribution_coverage_for_sources(
        state,
        task_id=contracted_leaf.id,
        source_deliverable_ids=[],
    )
    aggregate = contribution_coverage_for_sources(
        state,
        task_id=contracted_leaf.id,
        source_deliverable_ids=[child_deliverable.id],
    )

    assert direct["complete"] is True
    assert direct["required_contribution_task_ids"] == []
    assert aggregate["complete"] is False
    assert aggregate["unexpected_source_deliverable_ids"] == [child_deliverable.id]
    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_deliverable_submission(
            state,
            task_id=contracted_leaf.id,
            source_deliverable_ids=[child_deliverable.id],
        )
    assert raised.value.code == "contribution_coverage_incomplete"


def test_source_bearing_contracted_leaf_preserves_legacy_child_coverage() -> None:
    state = _state()
    contracted_leaf = AgentTask(
        **{
            **_support_task("legacy-contracted-leaf").model_dump(),
            "deliverable_contract": TaskDeliverableContract(
                require_git_provenance=True,
            ),
        }
    )
    child = _support_task(
        "legacy-child",
        state=TeamTaskState.FINISHED,
        parent_task_id=contracted_leaf.id,
    )
    state.tasks = {
        contracted_leaf.id: contracted_leaf,
        child.id: child,
    }
    child_deliverable = _delivered(
        state,
        deliverable_id="deliverable-legacy-child",
        task_id=child.id,
    )
    aggregate = _delivered(
        state,
        deliverable_id="deliverable-legacy-aggregate",
        task_id=contracted_leaf.id,
        source_deliverable_ids=[child_deliverable.id],
    )

    coverage = contribution_coverage_for_sources(
        state,
        task_id=contracted_leaf.id,
        source_deliverable_ids=list(aggregate.source_deliverable_ids),
    )

    assert coverage["complete"] is True
    assert coverage["required_contribution_task_ids"] == [child.id]
    assert coverage["required_source_deliverable_ids"] == [child_deliverable.id]


def test_contracted_leaf_cannot_require_integration_contributions() -> None:
    with pytest.raises(
        ValueError,
        match="leaf deliverable_contract cannot require contribution tasks",
    ):
        AgentTask(
            **{
                **_support_task("contracted-leaf").model_dump(),
                "deliverable_contract": TaskDeliverableContract(
                    require_git_provenance=True,
                    required_contribution_task_ids=["dependency"],
                ),
            }
        )


def test_scoped_integration_coverage_filters_task_and_deliverable_ids_separately() -> None:
    state = _state()
    integration = AgentTask(
        **{
            **_support_task("integration").model_dump(),
            "integration_boundary": True,
            "deliverable_contract": TaskDeliverableContract(
                required_contribution_task_ids=[
                    "visible-contribution",
                    "hidden-contribution",
                ],
            ),
        }
    )
    state.tasks = {
        integration.id: integration,
        "visible-contribution": _support_task(
            "visible-contribution",
            parent_task_id=integration.id,
        ),
        "hidden-contribution": _support_task(
            "hidden-contribution",
            parent_task_id=integration.id,
        ),
    }

    payload = task_payload_with_assessment(
        state,
        integration,
        visible_task_ids={integration.id, "visible-contribution"},
        visible_deliverable_ids=set(),
    )

    coverage = payload["assessment"]["contribution_coverage"]
    assert coverage["required_contribution_task_ids"] == ["visible-contribution"]
    assert coverage["missing_contribution_task_ids"] == ["visible-contribution"]


def test_integration_boundary_requires_authoritative_pinned_git_provenance() -> None:
    state = _state()
    task = AgentTask(
        **{
            **_support_task(
                "integration",
                state=TeamTaskState.WAITING_FOR_REVIEW,
            ).model_dump(),
            "integration_boundary": True,
            "deliverable_contract": TaskDeliverableContract(
                require_git_provenance=True,
            ),
        }
    )
    state.tasks[task.id] = task
    artifact_only = Deliverable(
        id="deliverable-artifact-only",
        task_id=task.id,
        producer_agent_id=task.owner_agent_id,
        summary="Aggregate without pinned Git provenance.",
        state=TeamRecordState.PENDING,
        acceptance_recipient_agent_id="manager",
    )
    state.deliverables[artifact_only.id] = artifact_only
    task.deliverable_ids = [artifact_only.id]

    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_deliverable_acceptance(state, artifact_only.id)

    assert raised.value.code == "task_git_deliverable_contract_unsatisfied"
    assert set(raised.value.correction_context["missing_git_ref_fields"]) == {
        "agent_branch",
        "branch_context_id",
        "commit_sha",
        "submitted_commit_sha",
    }

    commit_sha = "a" * 40
    state.git_work_contexts["context-integration"] = GitWorkContext(
        id="context-integration",
        agent_id=task.owner_agent_id,
        task_id=task.id,
        agent_branch="agents/integration/task",
        commit_sha=commit_sha,
        state=GitWorkContextState.ABANDONED,
    )
    git_deliverable = Deliverable(
        id="deliverable-git",
        task_id=task.id,
        producer_agent_id=task.owner_agent_id,
        summary="Pinned aggregate candidate.",
        state=TeamRecordState.PENDING,
        acceptance_recipient_agent_id="manager",
        git_refs=DeliverableGitRefs(
            branch_context_id="context-integration",
            agent_branch="agents/integration/task",
            commit_sha=commit_sha,
            submitted_commit_sha=commit_sha,
        ),
    )
    artifact_only.superseded_by_deliverable_id = git_deliverable.id
    state.deliverables[git_deliverable.id] = git_deliverable
    task.deliverable_ids.append(git_deliverable.id)

    with pytest.raises(TaskAssessmentBoundaryError) as abandoned:
        require_deliverable_acceptance(state, git_deliverable.id)
    assert (
        "branch_context_not_submitted"
        in abandoned.value.correction_context["invalid_git_provenance_reasons"]
    )

    context = state.git_work_contexts["context-integration"]
    context.state = GitWorkContextState.SUBMITTED
    context.commit_sha = "b" * 40
    with pytest.raises(TaskAssessmentBoundaryError) as mismatched:
        require_deliverable_acceptance(state, git_deliverable.id)
    assert (
        "branch_context_commit_mismatch"
        in mismatched.value.correction_context["invalid_git_provenance_reasons"]
    )

    context.commit_sha = commit_sha
    require_deliverable_acceptance(state, git_deliverable.id)
    assert _accept_deliverable_and_maybe_task(
        state,
        deliverable=git_deliverable,
        task=task,
        now=utc_now(),
    )
    assessment = assess_task(state, task.id)
    assert task.state == TeamTaskState.FINISHED
    assert assessment.resolution_status == TaskResolutionStatus.RESOLVED
    assert assessment.resolution_reason == "integration_deliverable_accepted"


def test_policy_v2_git_candidate_does_not_require_manifest_for_artifact_dependency() -> None:
    state = _state()
    dependency = _support_task("artifact-dependency", state=TeamTaskState.FINISHED)
    integration = AgentTask(
        **{
            **_support_task(
                "integration",
                state=TeamTaskState.WAITING_FOR_REVIEW,
                dependency_task_ids=[dependency.id],
            ).model_dump(),
            "integration_boundary": True,
            "deliverable_contract": TaskDeliverableContract(
                require_git_provenance=True,
            ),
        }
    )
    state.tasks = {dependency.id: dependency, integration.id: integration}
    _delivered(
        state,
        deliverable_id="artifact-dependency-deliverable",
        task_id=dependency.id,
    )
    commit_sha = "a" * 40
    state.git_work_contexts = {
        "context-integration": GitWorkContext(
            id="context-integration",
            agent_id=integration.owner_agent_id,
            task_id=integration.id,
            agent_branch="agents/integration/task",
            commit_sha=commit_sha,
            state=GitWorkContextState.SUBMITTED,
        )
    }
    candidate = Deliverable(
        id="integration-candidate",
        task_id=integration.id,
        producer_agent_id=integration.owner_agent_id,
        summary="Git candidate with an artifact-only lifecycle dependency.",
        state=TeamRecordState.PENDING,
        acceptance_policy_version=2,
        git_refs=DeliverableGitRefs(
            branch_context_id="context-integration",
            agent_branch="agents/integration/task",
            commit_sha=commit_sha,
            submitted_commit_sha=commit_sha,
        ),
        verification=DeliverableVerification(
            status="passed",
            provenance="server_release_equivalent",
            run_id="verification-artifact-dependency",
            candidate_commit_sha=commit_sha,
            evidence_refs=["test://artifact-dependency"],
        ),
    )
    state.deliverables[candidate.id] = candidate
    integration.deliverable_ids = [candidate.id]

    require_deliverable_acceptance(state, candidate.id)


def _policy_v2_mixed_source_candidate() -> tuple[TeamState, AgentTask, Deliverable]:
    state = _state()
    git_contribution = _support_task("git-contribution", state=TeamTaskState.FINISHED)
    artifact_contribution = _support_task(
        "artifact-contribution",
        state=TeamTaskState.FINISHED,
    )
    integration = AgentTask(
        **{
            **_support_task(
                "integration",
                state=TeamTaskState.WAITING_FOR_REVIEW,
                dependency_task_ids=[git_contribution.id, artifact_contribution.id],
            ).model_dump(),
            "integration_boundary": True,
            "deliverable_contract": TaskDeliverableContract(
                require_git_provenance=True,
                required_contribution_task_ids=[
                    git_contribution.id,
                    artifact_contribution.id,
                ],
            ),
        }
    )
    state.tasks = {
        git_contribution.id: git_contribution,
        artifact_contribution.id: artifact_contribution,
        integration.id: integration,
    }
    source_commit = "b" * 40
    git_source = _delivered(
        state,
        deliverable_id="git-source",
        task_id=git_contribution.id,
    )
    git_source.git_refs = DeliverableGitRefs(
        agent_branch="agents/source/git-contribution",
        commit_sha=source_commit,
        submitted_commit_sha=source_commit,
    )
    artifact_source = _delivered(
        state,
        deliverable_id="artifact-source",
        task_id=artifact_contribution.id,
    )
    candidate_commit = "c" * 40
    dependency_source = DependencyGitSource(
        dependency_task_id=git_contribution.id,
        deliverable_id=git_source.id,
        branch="agents/source/git-contribution",
        commit_sha=source_commit,
        relationship_kind="required_contribution",
    )
    manifest = DependencyCompositionManifest(
        target_task_id=integration.id,
        managed_repo_id="mr_0123456789abcdefabcd",
        base_branch="main",
        base_commit_sha="a" * 40,
        base_tree_hash="d" * 40,
        dependency_git_sources=[dependency_source],
        source_fingerprint="e" * 64,
        frontier_commit_shas=[source_commit],
        composed_commit_sha=candidate_commit,
        composed_tree_hash="f" * 40,
        verified_candidate_commit_sha=candidate_commit,
    )
    state.git_work_contexts = {
        "context-integration": GitWorkContext(
            id="context-integration",
            agent_id=integration.owner_agent_id,
            task_id=integration.id,
            agent_branch="agents/integration/task",
            commit_sha=candidate_commit,
            state=GitWorkContextState.SUBMITTED,
            dependency_git_sources=[dependency_source],
            dependency_composition_manifest=manifest,
        )
    }
    candidate = Deliverable(
        id="mixed-source-candidate",
        task_id=integration.id,
        producer_agent_id=integration.owner_agent_id,
        summary="Candidate composed from Git and artifact contributions.",
        state=TeamRecordState.PENDING,
        source_deliverable_ids=[git_source.id, artifact_source.id],
        acceptance_policy_version=2,
        dependency_composition_manifest=manifest,
        git_refs=DeliverableGitRefs(
            branch_context_id="context-integration",
            agent_branch="agents/integration/task",
            commit_sha=candidate_commit,
            submitted_commit_sha=candidate_commit,
        ),
        verification=DeliverableVerification(
            status="passed",
            provenance="server_release_equivalent",
            run_id="verification-mixed-sources",
            candidate_commit_sha=candidate_commit,
            evidence_refs=["test://mixed-sources"],
        ),
    )
    state.deliverables[candidate.id] = candidate
    integration.deliverable_ids = [candidate.id]

    return state, integration, candidate


def test_policy_v2_composition_scope_ignores_artifact_only_contribution() -> None:
    state, _integration, candidate = _policy_v2_mixed_source_candidate()

    require_deliverable_acceptance(state, candidate.id)


def test_policy_v2_composition_requires_server_verified_candidate_ancestry() -> None:
    state, _integration, candidate = _policy_v2_mixed_source_candidate()
    manifest = candidate.dependency_composition_manifest
    assert manifest is not None
    manifest.verified_candidate_commit_sha = "9" * 40

    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_deliverable_acceptance(state, candidate.id)

    assert raised.value.code == "dependency_composition_candidate_ancestry_unverified"
    assert raised.value.correction_context["candidate_commit_sha"] == "c" * 40
    assert raised.value.correction_context["required_ancestor_shas"] == [
        "c" * 40,
        "b" * 40,
    ]


def test_git_submission_verifies_and_records_composition_candidate_ancestry() -> None:
    state, integration, candidate = _policy_v2_mixed_source_candidate()
    state.projects["project"] = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        git=ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root",
        ),
    )
    context = state.git_work_contexts["context-integration"]
    context.state = GitWorkContextState.ACTIVE
    context.commit_sha = None
    manifest = context.dependency_composition_manifest
    assert manifest is not None
    manifest.verified_candidate_commit_sha = None
    resolver_calls: list[dict[str, object]] = []

    def _resolve(**kwargs: object) -> str:
        resolver_calls.append(kwargs)
        return candidate.git_refs.commit_sha or ""

    payload: dict[str, object] = {
        "branch_context_id": context.id,
        "git_refs": {
            "branch_context_id": context.id,
            "agent_branch": context.agent_branch,
            "commit_sha": candidate.git_refs.commit_sha,
        },
    }
    bind_deliverable_git_refs_to_work_context(
        state,
        agent_id=integration.owner_agent_id,
        task_id=integration.id,
        deliverable=payload,
        candidate_commit_resolver=_resolve,
    )

    assert resolver_calls == [
        {
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "directory_path": "root",
            "agent_branch": "agents/integration/task",
            "required_ancestor_shas": ["c" * 40, "b" * 40],
        }
    ]
    assert manifest.verified_candidate_commit_sha is None
    assert payload["_server_verified_composition_candidate_sha"] == "c" * 40


def test_policy_v2_composition_manifest_must_match_new_git_dependency() -> None:
    state, integration, candidate = _policy_v2_mixed_source_candidate()
    added_dependency = _support_task(
        "added-git-dependency",
        state=TeamTaskState.FINISHED,
    )
    state.tasks[added_dependency.id] = added_dependency
    added_source = _delivered(
        state,
        deliverable_id="added-git-source",
        task_id=added_dependency.id,
    )
    added_source.git_refs = DeliverableGitRefs(
        agent_branch="agents/source/added-git-dependency",
        commit_sha="8" * 40,
        submitted_commit_sha="8" * 40,
    )
    integration.dependency_task_ids.append(added_dependency.id)

    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_deliverable_acceptance(state, candidate.id)

    assert raised.value.code == "dependency_composition_manifest_dependency_scope_mismatch"
    assert raised.value.correction_context[
        "manifest_dependency_source_deliverable_ids"
    ] == []
    assert raised.value.correction_context[
        "current_dependency_source_deliverable_ids"
    ] == [added_source.id]


def test_integration_boundary_rejects_artifact_only_submission_before_side_effects() -> None:
    state = _state()
    task = AgentTask(
        **{
            **_support_task("integration").model_dump(),
            "integration_boundary": True,
            "deliverable_contract": TaskDeliverableContract(
                require_git_provenance=True,
            ),
        }
    )
    state.tasks[task.id] = task
    agent = PersistentAgent.model_construct(
        id=task.owner_agent_id,
        team_id="team",
    )

    with pytest.raises(TeamCommandValidationError) as raised:
        _validate_command_deliverable_submission_pre_publication(
            state,
            agent=agent,
            task=task,
            deliverable={
                "summary": "Artifact-only aggregate.",
                "artifact_refs": ["artifact:report"],
                "source_deliverable_ids": [],
                "git_refs": {},
                "outcomes": [],
            },
            deliverable_id="deliverable-artifact-only",
            dispatch_id=None,
        )

    assert raised.value.code == "task_git_deliverable_contract_unsatisfied"
    assert state.deliverables == {}


def test_ephemeral_acceptance_cannot_synthesize_incomplete_integration_deliverable() -> None:
    state = _state()
    task = AgentTask(
        **{
            **_support_task(
                "integration",
                dependency_task_ids=["required-contribution"],
            ).model_dump(),
            "integration_boundary": True,
            "deliverable_contract": TaskDeliverableContract(
                required_contribution_task_ids=["required-contribution"],
            ),
        }
    )
    state.tasks = {
        task.id: task,
        "required-contribution": _support_task(
            "required-contribution",
            state=TeamTaskState.FINISHED,
        ),
    }
    assignment = EphemeralAssignment(
        id="assignment",
        parent_agent_id=task.owner_agent_id,
        project_id="project",
        task_id=task.id,
        session_id="session-ephemeral",
        title="Worker",
        prompt="Produce a contribution.",
        state=EphemeralAssignmentState.COMPLETED,
        summary="Incomplete aggregate.",
        artifact_refs=["artifact:ephemeral"],
    )

    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        _apply_ephemeral_acceptance(
            state,
            assignment=assignment,
            accepted=True,
            deliverable_id="deliverable-ephemeral",
            rejection_reason=None,
        )

    assert raised.value.code == "contribution_coverage_incomplete"
    assert state.deliverables == {}
    assert task.deliverable_ids == []
    assert assignment.state == EphemeralAssignmentState.COMPLETED
    assert assignment.metadata == {}


def test_feature_consolidation_acknowledges_success_without_synthesizing_contract_deliverable() -> (
    None
):
    state = _state()
    task = AgentTask(
        **{
            **_support_task("integration").model_dump(),
            "integration_boundary": True,
            "deliverable_contract": TaskDeliverableContract(
                require_git_provenance=True,
            ),
        }
    )
    state.tasks[task.id] = task
    promotion = GitPromotionRecord(
        id="promotion",
        project_id="project",
        task_id=task.id,
        actor_agent_id=task.owner_agent_id,
        source_branch="team/team/reviews/source",
        target_branch="team/team/features/integration",
        promotion_kind="feature",
        state="succeeded",
        commit_sha="a" * 40,
    )
    result: dict[str, Any] = {
        "created_deliverable_ids": [],
        "updated_deliverable_ids": [],
        "resolved_blocker_ids": [],
    }

    _attach_promotion_success(
        state,
        promotion=promotion,
        commit_sha="a" * 40,
        result=result,
    )

    assert state.deliverables == {}
    assert task.deliverable_ids == []
    assert promotion.deliverable_id is None
    assert result["created_deliverable_ids"] == []
    assert result["diagnostics"] == [
        {
            "code": "integration_deliverable_submission_required",
            "task_id": task.id,
            "promotion_id": promotion.id,
            "message": (
                "Feature promotion succeeded, but an integration-boundary "
                "deliverable must be submitted explicitly so its full "
                "deliverable contract can be validated."
            ),
            "recommended_tool": "team_submit_deliverable_for_review",
        }
    ]


@pytest.mark.parametrize(
    ("first_state", "second_state"),
    [
        (TeamRecordState.BLOCKED, TeamRecordState.PENDING),
        (TeamRecordState.BLOCKED, TeamRecordState.ACCEPTED),
    ],
)
def test_sealed_cohort_cannot_be_replaced_with_unresolved_blocked_slot(
    first_state: TeamRecordState,
    second_state: TeamRecordState,
) -> None:
    state = _state()
    task = _support_task("task")
    state.tasks[task.id] = task
    predecessor = Deliverable(
        id="predecessor",
        task_id=task.id,
        producer_agent_id=task.owner_agent_id,
        summary="Blocked cohort candidate.",
        state=TeamRecordState.BLOCKED,
        review_request_ids=["review-first", "review-second"],
        review_cohort_id="cohort",
        review_cohort_sealed_at=utc_now(),
    )
    state.deliverables[predecessor.id] = predecessor
    state.review_requests = {
        "review-first": ReviewRequest(
            id="review-first",
            deliverable_id=predecessor.id,
            reviewer_agent_id="reviewer-first",
            state=first_state,
            review_cohort_id="cohort",
        ),
        "review-second": ReviewRequest(
            id="review-second",
            deliverable_id=predecessor.id,
            reviewer_agent_id="reviewer-second",
            state=second_state,
            review_cohort_id="cohort",
        ),
    }

    with pytest.raises(ValueError, match="unresolved review slots"):
        _superseded_deliverable_for_rework(
            state,
            task=task,
            producer_agent_id=task.owner_agent_id,
            supersedes_deliverable_id=predecessor.id,
        )


def test_finished_integration_boundary_without_accepted_deliverable_is_invalid() -> None:
    state = _state()
    task = AgentTask(
        **{
            **_support_task(
                "integration",
                state=TeamTaskState.FINISHED,
            ).model_dump(),
            "integration_boundary": True,
            "deliverable_contract": TaskDeliverableContract(),
        }
    )
    state.tasks[task.id] = task

    assessment = assess_task(state, task.id)

    assert assessment.resolution_status == TaskResolutionStatus.INVALID
    assert "integration_boundary_missing_accepted_deliverable" in assessment.invalid_reason_codes


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
    assert assess_task(state, task.id).resolution_reason == ("outcome_evidenced_noop_accepted")


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
    assert raised.value.correction_context["unresolved_descendant_ids"] == ["child"]
    assert state.deliverables == {}


def test_formal_review_cascade_defers_aggregate_with_unresolved_descendants() -> None:
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

    result: dict[str, Any] = {"errors": []}
    _cascade_review_decision(
        state,
        review=review,
        decision_state=TeamRecordState.ACCEPTED,
        findings=[],
        projects_to_recalc=set(),
        result=result,
    )

    assert result["deliverable_acceptance_deferred"]["unresolved_descendant_ids"] == ["child"]
    assert review.state == TeamRecordState.ACCEPTED
    assert review.decision_at is not None
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
