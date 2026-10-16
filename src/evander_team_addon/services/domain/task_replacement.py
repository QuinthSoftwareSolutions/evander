from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal, Mapping
from uuid import uuid4

from ...models import (
    AgentInboxItem,
    GitWorkContextState,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamState,
    TeamTaskState,
    TaskDeliverableContract,
    project_lead_agent_id,
)
from .case_task import _task_graph_edit_error
from .common import _NON_TERMINAL_TASK_STATES
from .preparation_failures import task_preparation_generation
from .review_handoff import queue_actionable_inbox_item
from .task_assessment import assess_task


TASK_DEPENDENCY_GRAPH_REPAIR_AUDIT_KIND = "task_dependency_graph_repair_audit"


class TaskDependencyRebindingError(ValueError):
    """A task replacement cannot preserve the current task-graph invariants."""

    def __init__(
        self,
        message: str,
        *,
        dependent_task_id: str | None = None,
        code: str = "task_replacement_graph_invalid",
        affected_task_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.dependent_task_id = dependent_task_id
        self.code = code
        self.affected_task_ids = affected_task_ids


@dataclass(frozen=True)
class TaskDependencyRebindingPlan:
    predecessor_task_id: str
    successor_task_id: str
    dependency_task_ids_by_dependent: Mapping[str, tuple[str, ...]]
    required_contribution_task_ids_by_integration_task: Mapping[str, tuple[str, ...]]
    integration_boundary_task_ids: tuple[str, ...]

    @property
    def dependent_task_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self.dependency_task_ids_by_dependent).union(
                    self.required_contribution_task_ids_by_integration_task
                )
            )
        )

    @property
    def affected_integration_boundary_task_ids(self) -> tuple[str, ...]:
        return self.integration_boundary_task_ids


def plan_task_dependency_rebinding(
    state: TeamState,
    *,
    predecessor_task_id: str,
    successor_task_id: str,
) -> TaskDependencyRebindingPlan:
    """Plan an atomic replacement of every active direct dependency edge.

    Terminal dependents retain their historical graph. The returned plan is
    validated against a simulated state so callers can apply every edge in one
    repository transaction without exposing a partially rebound graph.
    """

    predecessor = state.tasks.get(predecessor_task_id)
    if predecessor is None:
        raise TaskDependencyRebindingError(f"predecessor task {predecessor_task_id} does not exist")
    successor = state.tasks.get(successor_task_id)
    if successor is None:
        raise TaskDependencyRebindingError(f"successor task {successor_task_id} does not exist")
    if successor.id == predecessor.id or successor.project_id != predecessor.project_id:
        raise TaskDependencyRebindingError(
            "a task replacement successor must be a different task in "
            f"project {predecessor.project_id}"
        )
    if successor.state in {TeamTaskState.CANCELLED, TeamTaskState.REJECTED}:
        raise TaskDependencyRebindingError(
            f"task replacement successor {successor.id} is {successor.state.value}; "
            "a successor must not already be cancelled or rejected"
        )

    proposed_updates: dict[str, tuple[str, ...]] = {}
    proposed_contribution_updates: dict[str, tuple[str, ...]] = {}
    affected_integration_task_ids: set[str] = set()
    for dependent in sorted(state.tasks.values(), key=lambda item: item.id):
        if dependent.state not in _NON_TERMINAL_TASK_STATES:
            continue
        has_dependency_edge = predecessor.id in dependent.dependency_task_ids
        contract = dependent.deliverable_contract
        has_required_contribution_edge = bool(
            dependent.integration_boundary
            and contract is not None
            and predecessor.id in contract.required_contribution_task_ids
        )
        if not has_dependency_edge and not has_required_contribution_edge:
            continue
        if dependent.integration_boundary:
            affected_integration_task_ids.add(dependent.id)
            if dependent.deliverable_ids:
                raise TaskDependencyRebindingError(
                    f"cannot rebind integration task {dependent.id} after it submitted "
                    "a deliverable",
                    dependent_task_id=dependent.id,
                )
        if has_required_contribution_edge:
            replacement_contribution_ids: list[str] = []
            for contribution_id in contract.required_contribution_task_ids:
                replacement_id = (
                    successor.id if contribution_id == predecessor.id else contribution_id
                )
                if replacement_id not in replacement_contribution_ids:
                    replacement_contribution_ids.append(replacement_id)
            proposed_contribution_updates[dependent.id] = tuple(replacement_contribution_ids)

        replacement_ids: list[str] = []
        for dependency_id in dependent.dependency_task_ids:
            replacement_id = successor.id if dependency_id == predecessor.id else dependency_id
            if replacement_id not in replacement_ids:
                replacement_ids.append(replacement_id)
        if (
            has_required_contribution_edge
            and successor.parent_task_id != dependent.id
            and successor.id not in replacement_ids
        ):
            replacement_ids.append(successor.id)
        if replacement_ids != dependent.dependency_task_ids:
            proposed_updates[dependent.id] = tuple(replacement_ids)
        if dependent.mutation_policy is not None:
            locked_fields = set(dependent.mutation_policy.locked_fields)
            changed_locked_fields: list[str] = []
            if (
                dependent.id in proposed_updates
                and "dependency_task_ids" in locked_fields
            ):
                changed_locked_fields.append("dependency_task_ids")
            if (
                dependent.id in proposed_contribution_updates
                and "deliverable_contract" in locked_fields
            ):
                changed_locked_fields.append("deliverable_contract")
            if changed_locked_fields:
                raise TaskDependencyRebindingError(
                    f"cannot rebind workflow-managed dependent task {dependent.id}; "
                    "its mutation policy locks " + ", ".join(changed_locked_fields),
                    dependent_task_id=dependent.id,
                )

    simulated = state.model_copy(deep=True)
    for dependent_id in sorted(set(proposed_updates).union(proposed_contribution_updates)):
        dependent = simulated.tasks[dependent_id]
        replacement_ids = proposed_updates.get(
            dependent_id,
            tuple(dependent.dependency_task_ids),
        )
        replacement_contribution_ids = proposed_contribution_updates.get(dependent_id)
        if replacement_contribution_ids is not None:
            contract = dependent.deliverable_contract
            assert contract is not None
            dependent.deliverable_contract = TaskDeliverableContract(
                require_git_provenance=contract.require_git_provenance,
                required_contribution_task_ids=list(replacement_contribution_ids),
            )
        graph_error = _task_graph_edit_error(
            simulated,
            task_id=dependent.id,
            project_id=dependent.project_id,
            parent_task_id=dependent.parent_task_id,
            dependency_task_ids=list(replacement_ids),
        )
        if graph_error is not None:
            raise TaskDependencyRebindingError(
                f"cannot rebind dependent task {dependent.id}: {graph_error}",
                dependent_task_id=dependent.id,
            )
        dependent.dependency_task_ids = list(replacement_ids)

    return TaskDependencyRebindingPlan(
        predecessor_task_id=predecessor.id,
        successor_task_id=successor.id,
        dependency_task_ids_by_dependent=MappingProxyType(proposed_updates),
        required_contribution_task_ids_by_integration_task=MappingProxyType(
            proposed_contribution_updates
        ),
        integration_boundary_task_ids=tuple(sorted(affected_integration_task_ids)),
    )


def plan_cancelled_task_dependency_repair(
    state: TeamState,
    *,
    predecessor_task_id: str,
    successor_task_id: str,
    expected_dependent_task_ids: tuple[str, ...],
) -> TaskDependencyRebindingPlan:
    """Plan repair of active edges left by an earlier plain cancellation."""

    predecessor = state.tasks.get(predecessor_task_id)
    if predecessor is None:
        raise TaskDependencyRebindingError(
            f"cancelled predecessor task {predecessor_task_id} does not exist",
            code="task_dependency_repair_predecessor_not_found",
        )
    if predecessor.state != TeamTaskState.CANCELLED or predecessor.cancellation is None:
        raise TaskDependencyRebindingError(
            f"task {predecessor.id} is not cancelled with an audited disposition",
            code="task_dependency_repair_predecessor_not_cancelled",
        )
    recorded_successor_id = predecessor.cancellation.successor_task_id
    if recorded_successor_id != successor_task_id:
        raise TaskDependencyRebindingError(
            f"task {predecessor.id} records successor {recorded_successor_id!r}, "
            f"not requested successor {successor_task_id!r}",
            code="task_dependency_repair_successor_mismatch",
        )
    plan = plan_task_dependency_rebinding(
        state,
        predecessor_task_id=predecessor.id,
        successor_task_id=successor_task_id,
    )
    expected_ids = tuple(sorted(set(expected_dependent_task_ids)))
    actual_ids = plan.dependent_task_ids
    if actual_ids != expected_ids:
        prior_evidence = _existing_task_dependency_graph_repair_evidence(
            state,
            predecessor_task_id=predecessor.id,
            successor_task_id=successor_task_id,
        )
        repeated_completed_plan = (
            not actual_ids
            and prior_evidence is not None
            and tuple(
                prior_evidence.payload.get("expected_dependent_task_ids") or []
            )
            == expected_ids
        )
        if not repeated_completed_plan:
            raise TaskDependencyRebindingError(
                "cancelled-task dependency repair affected set changed; expected "
                f"{list(expected_ids)!r}, current {list(actual_ids)!r}",
                code="task_dependency_repair_affected_set_mismatch",
                affected_task_ids=actual_ids,
            )
    return plan


def apply_task_dependency_rebinding(
    state: TeamState,
    *,
    plan: TaskDependencyRebindingPlan,
    now: datetime,
) -> list[str]:
    """Apply a previously validated dependency-rebinding plan."""

    changed_ids: list[str] = []
    for dependent_id in plan.dependent_task_ids:
        dependent = state.tasks.get(dependent_id)
        if dependent is None:
            raise ValueError(f"dependent task {dependent_id} no longer exists")
        changed = False
        replacement_ids = plan.dependency_task_ids_by_dependent.get(dependent_id)
        if replacement_ids is not None:
            normalized_ids = list(replacement_ids)
            if dependent.dependency_task_ids != normalized_ids:
                dependent.dependency_task_ids = normalized_ids
                changed = True
        replacement_contribution_ids = plan.required_contribution_task_ids_by_integration_task.get(
            dependent_id
        )
        if replacement_contribution_ids is not None:
            contract = dependent.deliverable_contract
            if contract is None:
                raise ValueError(f"integration task {dependent_id} lost its deliverable contract")
            normalized_contribution_ids = list(replacement_contribution_ids)
            if contract.required_contribution_task_ids != normalized_contribution_ids:
                dependent.deliverable_contract = TaskDeliverableContract(
                    require_git_provenance=contract.require_git_provenance,
                    required_contribution_task_ids=normalized_contribution_ids,
                )
                changed = True
        if changed:
            dependent.updated_at = now
            changed_ids.append(dependent.id)
    return changed_ids


def _existing_task_dependency_graph_repair_evidence(
    state: TeamState,
    *,
    predecessor_task_id: str,
    successor_task_id: str,
) -> AgentInboxItem | None:
    records = (state.query_records(
        "inbox", "json_extract(payload_json,'$.kind')=? AND json_extract(payload_json,'$.predecessor_task_id')=? "
        "AND json_extract(payload_json,'$.successor_task_id')=?",
        (TASK_DEPENDENCY_GRAPH_REPAIR_AUDIT_KIND, predecessor_task_id, successor_task_id),
    ) if hasattr(state, "query_records") else state.inbox.values())
    matches = [
        item
        for item in records
        if item.payload.get("kind") == TASK_DEPENDENCY_GRAPH_REPAIR_AUDIT_KIND
        and item.payload.get("predecessor_task_id") == predecessor_task_id
        and item.payload.get("successor_task_id") == successor_task_id
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: (item.created_at, item.id))


def apply_cancelled_task_dependency_repair(
    state: TeamState,
    *,
    plan: TaskDependencyRebindingPlan,
    actor_kind: Literal["agent", "operator"],
    actor_id: str,
    reason: str,
    expected_dependent_task_ids: tuple[str, ...],
    result: dict[str, Any],
    now: datetime,
) -> tuple[list[str], bool]:
    """Apply a repair plan and retain durable action evidence without mutation history edits."""

    predecessor = state.tasks[plan.predecessor_task_id]
    project = state.projects[predecessor.project_id]
    prior_evidence = _existing_task_dependency_graph_repair_evidence(
        state,
        predecessor_task_id=plan.predecessor_task_id,
        successor_task_id=plan.successor_task_id,
    )
    evidence_will_be_created = bool(plan.dependent_task_ids) or prior_evidence is None
    audit_recipient_id: str | None = None
    if evidence_will_be_created:
        team = state.teams.get(project.team_id)
        manager_agent_id = team.manager_agent_id if team is not None else None
        audit_recipient_id = next(
            (
                candidate_id
                for candidate_id in (
                    project_lead_agent_id(state, project.id),
                    project.lead_agent_id,
                    manager_agent_id,
                )
                if candidate_id is not None and candidate_id in state.agents
            ),
            None,
        )
        if audit_recipient_id is None:
            raise TaskDependencyRebindingError(
                f"project {project.id} has no resolved lead or Team manager to retain "
                "dependency-repair action evidence",
                code="task_dependency_repair_audit_recipient_unavailable",
            )
    preparation_generations_before = {
        dependent_id: task_preparation_generation(state, dependent_id)
        for dependent_id in plan.dependent_task_ids
    }
    readiness_before = {
        dependent_id: assess_task(state, dependent_id).readiness.value
        for dependent_id in plan.dependent_task_ids
    }
    rebound_dependent_task_ids = apply_task_dependency_rebinding(
        state,
        plan=plan,
        now=now,
    )
    invalidated_git_context_ids_by_task = invalidate_rebound_integration_git_contexts(
        state,
        plan=plan,
        now=now,
    )
    invalidated_git_work_context_ids = sorted(
        context_id
        for context_ids in invalidated_git_context_ids_by_task.values()
        for context_id in context_ids
    )
    affected_integration_boundary_task_ids = list(
        plan.affected_integration_boundary_task_ids
    )
    result.update(
        {
            "predecessor_task_id": plan.predecessor_task_id,
            "successor_task_id": plan.successor_task_id,
            "expected_dependent_task_ids": list(expected_dependent_task_ids),
            "rebound_dependent_task_ids": rebound_dependent_task_ids,
            "affected_integration_boundary_task_ids": (
                affected_integration_boundary_task_ids
            ),
            "invalidated_git_work_context_ids": invalidated_git_work_context_ids,
            "invalidated_git_context_ids_by_task": invalidated_git_context_ids_by_task,
            "dependency_rebindings": [
                {
                    "dependent_task_id": dependent_id,
                    "predecessor_task_id": plan.predecessor_task_id,
                    "successor_task_id": plan.successor_task_id,
                    "dependency_task_ids": list(
                        state.tasks[dependent_id].dependency_task_ids
                    ),
                }
                for dependent_id in sorted(plan.dependency_task_ids_by_dependent)
            ],
            "required_contribution_rebindings": [
                {
                    "integration_task_id": integration_task_id,
                    "predecessor_task_id": plan.predecessor_task_id,
                    "successor_task_id": plan.successor_task_id,
                    "required_contribution_task_ids": list(contribution_ids),
                }
                for integration_task_id, contribution_ids in (
                    plan.required_contribution_task_ids_by_integration_task.items()
                )
            ],
            "preparation_generation_changes": {
                dependent_id: {
                    "before": preparation_generations_before[dependent_id],
                    "after": task_preparation_generation(state, dependent_id),
                }
                for dependent_id in rebound_dependent_task_ids
            },
            "newly_actionable_dependent_task_ids": [
                dependent_id
                for dependent_id in rebound_dependent_task_ids
                if readiness_before[dependent_id] != "actionable"
                and assess_task(state, dependent_id).readiness.value == "actionable"
            ],
            "repair_applied": bool(rebound_dependent_task_ids),
        }
    )
    recovery_inbox_ids = queue_rebound_integration_preparation_recovery(
        state,
        plan=plan,
        invalidated_git_context_ids_by_task=invalidated_git_context_ids_by_task,
        preparation_generations_before=preparation_generations_before,
        sender_agent_id=actor_id if actor_kind == "agent" else None,
        result=result,
        now=now,
    )
    evidence_created = bool(rebound_dependent_task_ids) or prior_evidence is None
    if evidence_created:
        assert audit_recipient_id is not None
        evidence = AgentInboxItem(
            id=f"audit-task-dependency-repair-{uuid4().hex}",
            recipient_agent_id=audit_recipient_id,
            sender_agent_id=actor_id if actor_kind == "agent" else None,
            task_id=predecessor.id,
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            response_requested=False,
            priority=TeamInboxPriority.NORMAL,
            state=TeamInboxItemState.HANDLED,
            payload={
                "kind": TASK_DEPENDENCY_GRAPH_REPAIR_AUDIT_KIND,
                "predecessor_task_id": plan.predecessor_task_id,
                "successor_task_id": plan.successor_task_id,
                "expected_dependent_task_ids": list(expected_dependent_task_ids),
                "actor_kind": actor_kind,
                "actor_id": actor_id,
                "reason": reason,
                "rebound_dependent_task_ids": rebound_dependent_task_ids,
                "affected_integration_boundary_task_ids": (
                    affected_integration_boundary_task_ids
                ),
                "invalidated_git_work_context_ids": invalidated_git_work_context_ids,
                "preparation_generation_changes": result[
                    "preparation_generation_changes"
                ],
                "created_preparation_recovery_inbox_item_ids": recovery_inbox_ids,
                "repaired_at": now.isoformat(),
            },
            created_at=now,
            updated_at=now,
        )
        state.inbox[evidence.id] = evidence
    else:
        assert prior_evidence is not None
        evidence = prior_evidence
    result.update(
        {
            "audit_action_evidence_id": evidence.id,
            "audit_evidence_reused": not evidence_created,
            "progress_made": evidence_created,
        }
    )
    changed_domain_ids = [
        *rebound_dependent_task_ids,
        *invalidated_git_work_context_ids,
        *recovery_inbox_ids,
        *([evidence.id] if evidence_created else []),
    ]
    return changed_domain_ids, evidence_created


def invalidate_rebound_integration_git_contexts(
    state: TeamState,
    *,
    plan: TaskDependencyRebindingPlan,
    now: datetime,
) -> dict[str, list[str]]:
    """Abandon active prepared contexts whose immutable source frontier changed."""

    integration_task_ids = set(plan.affected_integration_boundary_task_ids)
    invalidated_by_task: dict[str, list[str]] = {}
    for context in sorted(state.git_work_contexts.values(), key=lambda item: item.id):
        if (
            context.task_id not in integration_task_ids
            or context.state != GitWorkContextState.ACTIVE
        ):
            continue
        context.state = GitWorkContextState.ABANDONED
        context.updated_at = now
        invalidated_by_task.setdefault(context.task_id, []).append(context.id)
    return invalidated_by_task


def queue_rebound_integration_preparation_recovery(
    state: TeamState,
    *,
    plan: TaskDependencyRebindingPlan,
    invalidated_git_context_ids_by_task: Mapping[str, list[str]],
    preparation_generations_before: Mapping[str, str],
    sender_agent_id: str | None,
    result: dict[str, Any],
    now: datetime,
) -> list[str]:
    """Require a fresh preparation after abandoning an obsolete composition."""

    created_ids: list[str] = []
    for task_id in sorted(invalidated_git_context_ids_by_task):
        task = state.tasks.get(task_id)
        if task is None:
            continue
        invalidated_context_ids = list(invalidated_git_context_ids_by_task[task_id])
        item = queue_actionable_inbox_item(
            state,
            recipient_agent_id=task.owner_agent_id,
            sender_agent_id=sender_agent_id,
            task_id=task.id,
            priority=TeamInboxPriority.HIGH,
            payload={
                "kind": "task_replacement_preparation_recovery",
                "task_id": task.id,
                "predecessor_task_id": plan.predecessor_task_id,
                "successor_task_id": plan.successor_task_id,
                "invalidated_git_work_context_ids": invalidated_context_ids,
                "preparation_generation_before": preparation_generations_before.get(task.id),
                "preparation_generation_after": task_preparation_generation(state, task.id),
                "required_action": "reprepare_from_current_contribution_frontier",
                "summary": (
                    "A task replacement changed this integration boundary's source "
                    "frontier. Its prior Git preparation was abandoned; prepare a fresh "
                    "work context from the current successor contribution before "
                    "submitting work."
                ),
            },
            result=result,
            now=now,
        )
        created_ids.append(item.id)
    if created_ids:
        result.setdefault("created_preparation_recovery_inbox_item_ids", []).extend(created_ids)
    return created_ids


__all__ = [
    "TaskDependencyRebindingError",
    "TaskDependencyRebindingPlan",
    "TASK_DEPENDENCY_GRAPH_REPAIR_AUDIT_KIND",
    "apply_cancelled_task_dependency_repair",
    "apply_task_dependency_rebinding",
    "invalidate_rebound_integration_git_contexts",
    "plan_task_dependency_rebinding",
    "plan_cancelled_task_dependency_repair",
    "queue_rebound_integration_preparation_recovery",
]
