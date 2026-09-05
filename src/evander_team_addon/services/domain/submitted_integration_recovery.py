from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from evander.models import digest_payload

from ...git_naming import validate_branch_name
from ...models import (
    AgentInboxItem,
    AgentTask,
    GitWorkContextState,
    TaskCancellation,
    TaskDeliverableContract,
    TeamBlockerSourceKind,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamState,
    TeamTaskState,
    project_lead_agent_id,
)
from .blockers import (
    open_or_get_blocker,
    task_cancellation_blocking_handoff_ids,
)
from .case_task import (
    _link_task_to_case,
    _task_deliverable_contract_scope_error,
    _task_graph_edit_error,
)
from .common import _NON_TERMINAL_TASK_STATES, _set_task_state
from .task_assessment import assess_task
from .work_due import _mark_agent_work_due
from .work_lineage import ensure_task_work_lineage


SUBMITTED_INTEGRATION_RECOVERY_AUDIT_KIND = (
    "submitted_integration_boundary_recovery_audit"
)


class SubmittedIntegrationBoundaryRecoveryError(ValueError):
    """A submitted integration boundary cannot be recovered without losing history."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "submitted_integration_recovery_invalid",
        current_dependency_task_ids: tuple[str, ...] = (),
        current_required_contribution_task_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.current_dependency_task_ids = current_dependency_task_ids
        self.current_required_contribution_task_ids = (
            current_required_contribution_task_ids
        )


@dataclass(frozen=True)
class SubmittedIntegrationBoundaryRecoveryPlan:
    predecessor_task_id: str
    successor_task_id: str
    integration_task_id: str
    replacement_task_id: str
    expected_dependency_task_ids: tuple[str, ...]
    expected_required_contribution_task_ids: tuple[str, ...]
    replacement_dependency_task_ids: tuple[str, ...]
    replacement_required_contribution_task_ids: tuple[str, ...]
    git_base_branch: str | None
    git_base_commit_sha: str | None
    reason: str
    request_fingerprint: str
    terminalization_block_reasons: tuple[str, ...]
    replay_evidence_id: str | None = None

    @property
    def is_replay(self) -> bool:
        return self.replay_evidence_id is not None

    @property
    def terminalize_old_boundary(self) -> bool:
        return not self.terminalization_block_reasons


def plan_submitted_integration_boundary_recovery(
    state: TeamState,
    *,
    predecessor_task_id: str,
    successor_task_id: str,
    integration_task_id: str,
    replacement_task_id: str,
    expected_dependency_task_ids: tuple[str, ...],
    expected_required_contribution_task_ids: tuple[str, ...],
    reason: str,
    git_base_commit_sha: str | None,
    git_base_branch: str | None,
    actor_kind: Literal["agent", "operator"],
) -> SubmittedIntegrationBoundaryRecoveryPlan:
    predecessor = state.tasks.get(predecessor_task_id)
    if predecessor is None:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"cancelled predecessor task {predecessor_task_id} does not exist",
            code="submitted_integration_recovery_predecessor_not_found",
        )
    if predecessor.state != TeamTaskState.CANCELLED or predecessor.cancellation is None:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"task {predecessor.id} is not cancelled with an audited disposition",
            code="submitted_integration_recovery_predecessor_not_cancelled",
        )
    if predecessor.cancellation.successor_task_id != successor_task_id:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"task {predecessor.id} records successor "
            f"{predecessor.cancellation.successor_task_id!r}, not requested successor "
            f"{successor_task_id!r}",
            code="submitted_integration_recovery_successor_mismatch",
        )
    successor = state.tasks.get(successor_task_id)
    if successor is None:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"recorded successor task {successor_task_id} does not exist",
            code="submitted_integration_recovery_successor_not_found",
        )
    if successor.project_id != predecessor.project_id:
        raise SubmittedIntegrationBoundaryRecoveryError(
            "the cancelled predecessor and recorded successor must share a project",
            code="submitted_integration_recovery_successor_mismatch",
        )
    if successor.state in {TeamTaskState.CANCELLED, TeamTaskState.REJECTED}:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"recorded successor {successor.id} is {successor.state.value}",
            code="submitted_integration_recovery_successor_dead_end",
        )

    integration = state.tasks.get(integration_task_id)
    if integration is None:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"integration boundary task {integration_task_id} does not exist",
            code="submitted_integration_recovery_boundary_not_found",
        )
    if integration.project_id != predecessor.project_id:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"integration boundary {integration.id} is not in project "
            f"{predecessor.project_id}",
            code="submitted_integration_recovery_project_mismatch",
        )
    contract = integration.deliverable_contract
    if not integration.integration_boundary or contract is None:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"task {integration.id} is not an integration boundary",
            code="submitted_integration_recovery_not_integration_boundary",
        )

    expected_dependencies = tuple(sorted(set(expected_dependency_task_ids)))
    expected_contributions = tuple(
        sorted(set(expected_required_contribution_task_ids))
    )
    current_dependencies = tuple(sorted(set(integration.dependency_task_ids)))
    current_contributions = tuple(
        sorted(set(contract.required_contribution_task_ids))
    )
    if (
        expected_dependencies != current_dependencies
        or expected_contributions != current_contributions
    ):
        raise SubmittedIntegrationBoundaryRecoveryError(
            "submitted integration recovery frontier changed; expected dependencies "
            f"{list(expected_dependencies)!r} and contributions "
            f"{list(expected_contributions)!r}, current dependencies "
            f"{list(current_dependencies)!r} and contributions "
            f"{list(current_contributions)!r}",
            code="submitted_integration_recovery_frontier_mismatch",
            current_dependency_task_ids=current_dependencies,
            current_required_contribution_task_ids=current_contributions,
        )
    if (
        predecessor.id not in current_dependencies
        and predecessor.id not in current_contributions
    ):
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"integration boundary {integration.id} does not contain cancelled "
            f"predecessor {predecessor.id} in its current frontier",
            code="submitted_integration_recovery_predecessor_not_in_frontier",
        )

    normalized_branch, normalized_commit = _normalized_recovery_git_pin(
        integration,
        require_git_provenance=contract.require_git_provenance,
        git_base_branch=git_base_branch,
        git_base_commit_sha=git_base_commit_sha,
    )
    replacement_dependencies = _replace_frontier_id(
        integration.dependency_task_ids,
        predecessor_task_id=predecessor.id,
        successor_task_id=successor.id,
    )
    replacement_contributions = _replace_frontier_id(
        contract.required_contribution_task_ids,
        predecessor_task_id=predecessor.id,
        successor_task_id=successor.id,
    )
    for contribution_task_id in replacement_contributions:
        if contribution_task_id not in replacement_dependencies:
            replacement_dependencies = (*replacement_dependencies, contribution_task_id)

    spec = {
        "predecessor_task_id": predecessor.id,
        "successor_task_id": successor.id,
        "integration_task_id": integration.id,
        "replacement_task_id": replacement_task_id,
        "expected_dependency_task_ids": list(expected_dependencies),
        "expected_required_contribution_task_ids": list(expected_contributions),
        "replacement_dependency_task_ids": list(replacement_dependencies),
        "replacement_required_contribution_task_ids": list(replacement_contributions),
        "git_base_branch": normalized_branch,
        "git_base_commit_sha": normalized_commit,
        "reason": reason,
    }
    request_fingerprint = digest_payload(spec)
    prior_evidence = _existing_recovery_evidence(
        state,
        integration_task_id=integration.id,
    )
    if (
        prior_evidence is not None
        and prior_evidence.payload.get("replacement_integration_task_id")
        != replacement_task_id
    ):
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"integration boundary {integration.id} already recovered to "
            f"{prior_evidence.payload.get('replacement_integration_task_id')}",
            code="submitted_integration_recovery_replay_mismatch",
        )
    existing_replacement = state.tasks.get(replacement_task_id)
    if prior_evidence is not None or existing_replacement is not None:
        if prior_evidence is None or existing_replacement is None:
            raise SubmittedIntegrationBoundaryRecoveryError(
                f"replacement boundary {replacement_task_id} has incomplete recovery "
                "lineage evidence",
                code="submitted_integration_recovery_replay_mismatch",
            )
        if prior_evidence.payload.get("request_fingerprint") != request_fingerprint:
            raise SubmittedIntegrationBoundaryRecoveryError(
                f"replacement boundary {replacement_task_id} already belongs to a "
                "different submitted-integration recovery request",
                code="submitted_integration_recovery_replay_mismatch",
            )
        _require_replay_lineage_matches(
            existing_replacement,
            integration=integration,
            replacement_dependencies=replacement_dependencies,
            replacement_contributions=replacement_contributions,
            git_base_branch=normalized_branch,
            git_base_commit_sha=normalized_commit,
        )
        return SubmittedIntegrationBoundaryRecoveryPlan(
            predecessor_task_id=predecessor.id,
            successor_task_id=successor.id,
            integration_task_id=integration.id,
            replacement_task_id=existing_replacement.id,
            expected_dependency_task_ids=expected_dependencies,
            expected_required_contribution_task_ids=expected_contributions,
            replacement_dependency_task_ids=replacement_dependencies,
            replacement_required_contribution_task_ids=replacement_contributions,
            git_base_branch=normalized_branch,
            git_base_commit_sha=normalized_commit,
            reason=reason,
            request_fingerprint=request_fingerprint,
            terminalization_block_reasons=tuple(
                prior_evidence.payload.get("old_boundary_retention_reasons") or []
            ),
            replay_evidence_id=prior_evidence.id,
        )

    if integration.state not in _NON_TERMINAL_TASK_STATES:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"integration boundary {integration.id} is already {integration.state.value}",
            code="submitted_integration_recovery_boundary_terminal",
        )
    has_retained_git_context = any(
        context.task_id == integration.id
        for context in state.git_work_contexts.values()
    )
    if not integration.deliverable_ids and not (
        contract.require_git_provenance and has_retained_git_context
    ):
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"integration boundary {integration.id} has no submitted deliverable or "
            "retained Git preparation history",
            code="submitted_integration_recovery_not_submitted",
        )
    if integration.mutation_policy is not None and integration.mutation_policy.locked_fields:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"integration boundary {integration.id} mutation policy "
            f"{integration.mutation_policy.policy_id} locks recovery fields: "
            + ", ".join(integration.mutation_policy.locked_fields),
            code="submitted_integration_recovery_mutation_policy_locked",
        )
    graph_error = _task_graph_edit_error(
        state,
        task_id=replacement_task_id,
        project_id=integration.project_id,
        parent_task_id=integration.parent_task_id,
        dependency_task_ids=list(replacement_dependencies),
    )
    if graph_error is not None:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"cannot create replacement integration boundary {replacement_task_id}: "
            f"{graph_error}",
            code="submitted_integration_recovery_graph_invalid",
        )
    replacement_contract = TaskDeliverableContract(
        require_git_provenance=contract.require_git_provenance,
        required_contribution_task_ids=list(replacement_contributions),
    )
    contract_error = _task_deliverable_contract_scope_error(
        state,
        task_id=replacement_task_id,
        project_id=integration.project_id,
        dependency_task_ids=list(replacement_dependencies),
        integration_boundary=True,
        deliverable_contract=replacement_contract,
    )
    if contract_error is not None:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"cannot create replacement integration boundary {replacement_task_id}: "
            f"{contract_error}",
            code="submitted_integration_recovery_graph_invalid",
        )

    return SubmittedIntegrationBoundaryRecoveryPlan(
        predecessor_task_id=predecessor.id,
        successor_task_id=successor.id,
        integration_task_id=integration.id,
        replacement_task_id=replacement_task_id,
        expected_dependency_task_ids=expected_dependencies,
        expected_required_contribution_task_ids=expected_contributions,
        replacement_dependency_task_ids=replacement_dependencies,
        replacement_required_contribution_task_ids=replacement_contributions,
        git_base_branch=normalized_branch,
        git_base_commit_sha=normalized_commit,
        reason=reason,
        request_fingerprint=request_fingerprint,
        terminalization_block_reasons=_terminalization_block_reasons(
            state,
            integration=integration,
            actor_kind=actor_kind,
        ),
    )


def apply_submitted_integration_boundary_recovery(
    state: TeamState,
    *,
    plan: SubmittedIntegrationBoundaryRecoveryPlan,
    actor_kind: Literal["agent", "operator"],
    actor_id: str,
    result: dict[str, Any],
    now: datetime,
) -> tuple[list[str], bool]:
    integration = state.tasks[plan.integration_task_id]
    if plan.is_replay:
        assert plan.replay_evidence_id is not None
        evidence = state.inbox[plan.replay_evidence_id]
        _update_result_from_evidence(result, evidence=evidence, replayed=True)
        return [], False

    project = state.projects[integration.project_id]
    audit_recipient_id = _audit_recipient_id(state, project_id=project.id)
    replacement_contract = TaskDeliverableContract(
        require_git_provenance=integration.deliverable_contract.require_git_provenance,
        required_contribution_task_ids=list(
            plan.replacement_required_contribution_task_ids
        ),
    )
    replacement = AgentTask(
        id=plan.replacement_task_id,
        owner_agent_id=integration.owner_agent_id,
        project_id=integration.project_id,
        case_id=integration.case_id,
        priority=integration.priority,
        state=TeamTaskState.PENDING,
        task_kind=integration.task_kind,
        outcome_contract=(
            integration.outcome_contract.model_copy(deep=True)
            if integration.outcome_contract is not None
            else None
        ),
        integration_boundary=True,
        deliverable_contract=replacement_contract,
        completion=None,
        state_entered_at=now,
        title=integration.title,
        description=integration.description,
        created_by_agent_id=actor_id if actor_kind == "agent" else None,
        created_by_actor=actor_id if actor_kind == "operator" else None,
        parent_task_id=integration.parent_task_id,
        originating_task_id=integration.id,
        authored_by=integration.authored_by.model_copy(deep=True) if integration.authored_by else None,
        git_base_branch=plan.git_base_branch,
        git_base_commit_sha=plan.git_base_commit_sha,
        mutation_policy=(
            integration.mutation_policy.model_copy(deep=True)
            if integration.mutation_policy is not None
            else None
        ),
        cancellation=None,
        dependency_task_ids=list(plan.replacement_dependency_task_ids),
        deliverable_ids=[],
        source_evidence_snapshot_ids=list(integration.source_evidence_snapshot_ids),
        created_at=now,
        updated_at=now,
    )
    ensure_task_work_lineage(state, replacement)
    state.tasks[replacement.id] = replacement
    if replacement.case_id is not None:
        _link_task_to_case(
            state,
            task_id=replacement.id,
            case_id=replacement.case_id,
            now=now,
        )

    old_boundary_terminalized = plan.terminalize_old_boundary
    blocker_id: str | None = None
    if old_boundary_terminalized:
        integration.cancellation = TaskCancellation(
            reason_code="submitted_integration_boundary_replaced",
            reason=plan.reason,
            actor_kind=actor_kind,
            actor_id=actor_id,
            successor_task_id=replacement.id,
            cancelled_at=now,
        )
        _set_task_state(integration, TeamTaskState.CANCELLED, now=now)
        integration.updated_at = now
    else:
        blocker, _created = open_or_get_blocker(
            state,
            source_kind=TeamBlockerSourceKind.EXPLICIT,
            source_id=(
                f"submitted-integration-boundary-recovery:{integration.id}:"
                f"{replacement.id}"
            ),
            reason_code="submitted_integration_boundary_history_retained",
            summary=(
                f"Submitted integration boundary {integration.id} requires "
                "explicit historical disposition"
            ),
            details=(
                f"Replacement boundary {replacement.id} was created without changing "
                f"the submitted history of {integration.id}. Team retained the old "
                "boundary because: "
                + "; ".join(plan.terminalization_block_reasons)
            ),
            priority=TeamInboxPriority.HIGH,
            reporter_agent_id=actor_id if actor_kind == "agent" else None,
            reporter_actor=actor_id if actor_kind == "operator" else None,
            initial_owner_agent_id=audit_recipient_id,
            route_initial_owner_even_if_unavailable=True,
            team_id=project.team_id,
            project_id=project.id,
            task_id=integration.id,
            affected_task_ids=[integration.id, replacement.id],
            result=result,
            now=now,
        )
        blocker_id = blocker.id

    retained_deliverable_ids = list(integration.deliverable_ids)
    retained_review_request_ids = sorted(
        review.id
        for review in state.review_requests.values()
        if review.deliverable_id in set(retained_deliverable_ids)
    )
    retained_git_work_context_ids = sorted(
        context.id
        for context in state.git_work_contexts.values()
        if context.task_id == integration.id
    )
    evidence = AgentInboxItem(
        id=f"audit-submitted-integration-recovery-{uuid4().hex}",
        recipient_agent_id=audit_recipient_id,
        sender_agent_id=actor_id if actor_kind == "agent" else None,
        task_id=integration.id,
        item_type=TeamInboxItemType.PROGRESS_REPORT,
        response_requested=False,
        priority=TeamInboxPriority.HIGH,
        state=TeamInboxItemState.HANDLED,
        payload={
            "kind": SUBMITTED_INTEGRATION_RECOVERY_AUDIT_KIND,
            "request_fingerprint": plan.request_fingerprint,
            "predecessor_task_id": plan.predecessor_task_id,
            "successor_task_id": plan.successor_task_id,
            "old_integration_task_id": integration.id,
            "replacement_integration_task_id": replacement.id,
            "expected_dependency_task_ids": list(
                plan.expected_dependency_task_ids
            ),
            "expected_required_contribution_task_ids": list(
                plan.expected_required_contribution_task_ids
            ),
            "replacement_dependency_task_ids": list(
                plan.replacement_dependency_task_ids
            ),
            "replacement_required_contribution_task_ids": list(
                plan.replacement_required_contribution_task_ids
            ),
            "git_base_branch": plan.git_base_branch,
            "git_base_commit_sha": plan.git_base_commit_sha,
            "reason": plan.reason,
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "old_boundary_terminalized": old_boundary_terminalized,
            "old_boundary_blocker_id": blocker_id,
            "old_boundary_retention_reasons": list(
                plan.terminalization_block_reasons
            ),
            "retained_deliverable_ids": retained_deliverable_ids,
            "retained_review_request_ids": retained_review_request_ids,
            "retained_git_work_context_ids": retained_git_work_context_ids,
            "recovered_at": now.isoformat(),
        },
        created_at=now,
        updated_at=now,
    )
    state.inbox[evidence.id] = evidence
    if assess_task(state, replacement.id).readiness.value == "actionable":
        _mark_agent_work_due(state, agent_id=replacement.owner_agent_id, now=now)

    _update_result_from_evidence(result, evidence=evidence, replayed=False)
    changed_ids = [replacement.id, evidence.id]
    if old_boundary_terminalized:
        changed_ids.append(integration.id)
    if blocker_id is not None:
        changed_ids.append(blocker_id)
    changed_ids.extend(result.get("created_inbox_item_ids") or [])
    changed_ids.extend(result.get("blocker_event_ids") or [])
    return list(dict.fromkeys(changed_ids)), True


def _normalized_recovery_git_pin(
    integration: AgentTask,
    *,
    require_git_provenance: bool,
    git_base_branch: str | None,
    git_base_commit_sha: str | None,
) -> tuple[str | None, str | None]:
    normalized_branch = str(git_base_branch or "").strip() or None
    normalized_commit = str(git_base_commit_sha or "").strip().casefold() or None
    if not require_git_provenance:
        if normalized_branch is not None or normalized_commit is not None:
            raise SubmittedIntegrationBoundaryRecoveryError(
                "non-Git integration recovery must not provide Git base fields",
                code="submitted_integration_recovery_unexpected_git_pin",
            )
        return None, None
    if normalized_commit is None:
        raise SubmittedIntegrationBoundaryRecoveryError(
            "Git-backed integration recovery requires git_base_commit_sha",
            code="submitted_integration_recovery_git_pin_required",
        )
    if len(normalized_commit) not in {40, 64} or any(
        char not in "0123456789abcdef" for char in normalized_commit
    ):
        raise SubmittedIntegrationBoundaryRecoveryError(
            "git_base_commit_sha must be a full Git object ID",
            code="submitted_integration_recovery_git_pin_required",
        )
    if normalized_branch is not None:
        normalized_branch = validate_branch_name(normalized_branch)
    else:
        normalized_branch = integration.git_base_branch
    if normalized_branch is None:
        raise SubmittedIntegrationBoundaryRecoveryError(
            "Git-backed integration recovery requires git_base_branch when the old "
            "boundary has no pinned branch",
            code="submitted_integration_recovery_git_branch_required",
        )
    return normalized_branch, normalized_commit


def _replace_frontier_id(
    values: list[str],
    *,
    predecessor_task_id: str,
    successor_task_id: str,
) -> tuple[str, ...]:
    replaced: list[str] = []
    for value in values:
        replacement = successor_task_id if value == predecessor_task_id else value
        if replacement not in replaced:
            replaced.append(replacement)
    return tuple(replaced)


def _terminalization_block_reasons(
    state: TeamState,
    *,
    integration: AgentTask,
    actor_kind: Literal["agent", "operator"],
) -> tuple[str, ...]:
    reasons: list[str] = []
    assessment = assess_task(state, integration.id)
    if assessment.unresolved_descendant_ids:
        reasons.append(
            "unresolved descendants: " + ", ".join(assessment.unresolved_descendant_ids)
        )
    blocking_acceptance_ids, blocking_review_ids = (
        task_cancellation_blocking_handoff_ids(
            state,
            acceptance_ids=assessment.active_acceptance_ids,
            review_request_ids=assessment.active_review_request_ids,
        )
    )
    if blocking_acceptance_ids:
        reasons.append(
            "active acceptance handoffs: " + ", ".join(blocking_acceptance_ids)
        )
    if blocking_review_ids:
        reasons.append("active review handoffs: " + ", ".join(blocking_review_ids))
    active_context_ids = sorted(
        context.id
        for context in state.git_work_contexts.values()
        if context.task_id == integration.id
        and context.state == GitWorkContextState.ACTIVE
    )
    if active_context_ids:
        reasons.append("active Git contexts: " + ", ".join(active_context_ids))
    active_blocker_ids = sorted(assessment.active_blocker_ids)
    if active_blocker_ids:
        reasons.append("active blockers: " + ", ".join(active_blocker_ids))
    downstream_ids = sorted(
        task.id
        for task in state.tasks.values()
        if task.id != integration.id
        and task.state in _NON_TERMINAL_TASK_STATES
        and (
            integration.id in task.dependency_task_ids
            or (
                task.integration_boundary
                and task.deliverable_contract is not None
                and integration.id
                in task.deliverable_contract.required_contribution_task_ids
            )
        )
    )
    if downstream_ids:
        reasons.append("active downstream boundaries/tasks: " + ", ".join(downstream_ids))
    if (
        actor_kind == "agent"
        and integration.mutation_policy is not None
        and not integration.mutation_policy.allow_agent_cancellation
    ):
        reasons.append("mutation policy forbids agent cancellation")
    return tuple(reasons)


def _existing_recovery_evidence(
    state: TeamState,
    *,
    integration_task_id: str,
) -> AgentInboxItem | None:
    matches = [
        item
        for item in state.inbox.values()
        if item.payload.get("kind") == SUBMITTED_INTEGRATION_RECOVERY_AUDIT_KIND
        and item.payload.get("old_integration_task_id") == integration_task_id
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: (item.created_at, item.id))


def _require_replay_lineage_matches(
    replacement: AgentTask,
    *,
    integration: AgentTask,
    replacement_dependencies: tuple[str, ...],
    replacement_contributions: tuple[str, ...],
    git_base_branch: str | None,
    git_base_commit_sha: str | None,
) -> None:
    contract = replacement.deliverable_contract
    lineage_matches = (
        replacement.originating_task_id == integration.id
        and replacement.project_id == integration.project_id
        and replacement.case_id == integration.case_id
        and replacement.parent_task_id == integration.parent_task_id
        and replacement.owner_agent_id == integration.owner_agent_id
        and replacement.task_kind == integration.task_kind
        and replacement.priority == integration.priority
        and replacement.title == integration.title
        and replacement.description == integration.description
        and replacement.outcome_contract == integration.outcome_contract
        and replacement.integration_boundary
        and contract is not None
        and contract.require_git_provenance
        == integration.deliverable_contract.require_git_provenance
        and tuple(replacement.dependency_task_ids) == replacement_dependencies
        and tuple(contract.required_contribution_task_ids)
        == replacement_contributions
        and replacement.git_base_branch == git_base_branch
        and replacement.git_base_commit_sha == git_base_commit_sha
        and replacement.source_evidence_snapshot_ids
        == integration.source_evidence_snapshot_ids
        and replacement.mutation_policy == integration.mutation_policy
    )
    if not lineage_matches:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"replacement boundary {replacement.id} no longer matches its frozen "
            "submitted-integration recovery lineage",
            code="submitted_integration_recovery_replay_mismatch",
        )


def _audit_recipient_id(state: TeamState, *, project_id: str) -> str:
    project = state.projects[project_id]
    team = state.teams.get(project.team_id)
    manager_agent_id = team.manager_agent_id if team is not None else None
    recipient_id = next(
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
    if recipient_id is None:
        raise SubmittedIntegrationBoundaryRecoveryError(
            f"project {project.id} has no resolved lead or Team manager to retain "
            "submitted-integration recovery evidence",
            code="submitted_integration_recovery_audit_recipient_unavailable",
        )
    return recipient_id


def _update_result_from_evidence(
    result: dict[str, Any],
    *,
    evidence: AgentInboxItem,
    replayed: bool,
) -> None:
    payload = evidence.payload
    result.update(
        {
            "predecessor_task_id": payload["predecessor_task_id"],
            "successor_task_id": payload["successor_task_id"],
            "old_integration_task_id": payload["old_integration_task_id"],
            "replacement_integration_task_id": payload[
                "replacement_integration_task_id"
            ],
            "created_task_ids": (
                [] if replayed else [payload["replacement_integration_task_id"]]
            ),
            "expected_dependency_task_ids": payload[
                "expected_dependency_task_ids"
            ],
            "expected_required_contribution_task_ids": payload[
                "expected_required_contribution_task_ids"
            ],
            "replacement_dependency_task_ids": payload[
                "replacement_dependency_task_ids"
            ],
            "replacement_required_contribution_task_ids": payload[
                "replacement_required_contribution_task_ids"
            ],
            "git_base_branch": payload.get("git_base_branch"),
            "git_base_commit_sha": payload.get("git_base_commit_sha"),
            "old_boundary_terminalized": payload["old_boundary_terminalized"],
            "old_boundary_blocker_id": payload.get("old_boundary_blocker_id"),
            "old_boundary_retention_reasons": payload.get(
                "old_boundary_retention_reasons"
            )
            or [],
            "retained_deliverable_ids": payload["retained_deliverable_ids"],
            "retained_review_request_ids": payload[
                "retained_review_request_ids"
            ],
            "retained_git_work_context_ids": payload[
                "retained_git_work_context_ids"
            ],
            "audit_action_evidence_id": evidence.id,
            "request_fingerprint": payload["request_fingerprint"],
            "recovery_applied": not replayed,
            "replayed": replayed,
            "progress_made": not replayed,
        }
    )


__all__ = [
    "SUBMITTED_INTEGRATION_RECOVERY_AUDIT_KIND",
    "SubmittedIntegrationBoundaryRecoveryError",
    "SubmittedIntegrationBoundaryRecoveryPlan",
    "apply_submitted_integration_boundary_recovery",
    "plan_submitted_integration_boundary_recovery",
]
