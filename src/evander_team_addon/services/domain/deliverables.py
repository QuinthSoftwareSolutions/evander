from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import datetime
from typing import Any
from ...git_naming import validate_branch_name
from ...models import (
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    DeliverableOutcome,
    DeliverableVerification,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamRecordState,
    TeamState,
    TeamTaskKind,
    TeamTaskState,
    TaskCompletion,
    TaskCompletionKind,
)

from .common import (
    _optional_mapping,
    _optional_text,
    _required_text,
    _set_task_state,
    _string_list,
)
from .git_work_contexts import finalize_active_git_work_contexts_for_task
from .initial_release import initialize_pending_initial_release_disposition
from .task_supervision import (
    queue_parent_resolution_transitions,
    queue_root_task_resolution,
)


_DELIVERABLE_VERIFICATION_STATUSES = frozenset(
    {"passed", "failed", "partial", "not_run", "unknown"}
)


def _deliverable_git_refs_from_payload(value: object) -> DeliverableGitRefs:
    payload = _optional_mapping(value, "git_refs") or {}
    branch_context_id = _optional_text(payload.get("branch_context_id"))
    agent_branch = _optional_text(payload.get("agent_branch"))
    if agent_branch is not None:
        agent_branch = validate_branch_name(agent_branch)
    return DeliverableGitRefs(
        branch_context_id=branch_context_id,
        agent_branch=agent_branch,
        commit_sha=_optional_text(payload.get("commit_sha")),
        submitted_commit_sha=_optional_text(payload.get("submitted_commit_sha")),
    )


def _normalize_deliverable_verification_mapping(
    value: object,
    *,
    field_name: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    payload = _optional_mapping(value, field_name) or {}
    status = _required_text(payload.get("status"), f"{field_name}.status").lower()
    if status not in _DELIVERABLE_VERIFICATION_STATUSES:
        allowed = ", ".join(sorted(_DELIVERABLE_VERIFICATION_STATUSES))
        raise ValueError(f"{field_name}.status must be one of: {allowed}")
    return {
        "status": status,
        "summary": _optional_text(payload.get("summary")),
        "evidence_refs": _string_list(
            payload.get("evidence_refs"),
            f"{field_name}.evidence_refs",
        ),
    }


def _deliverable_verification_from_payload(
    value: object,
) -> DeliverableVerification | None:
    payload = _normalize_deliverable_verification_mapping(
        value,
        field_name="verification",
    )
    if payload is None:
        return None
    return DeliverableVerification.model_validate(payload)


def _deliverable_verification_payload(deliverable: Deliverable) -> dict[str, Any] | None:
    if deliverable.verification is None:
        return None
    return deliverable.verification.model_dump(mode="json")


def _deliverable_submission_next_action_hints(
    *,
    deliverable_id: str,
    deliverable: Mapping[str, Any],
    created_inbox_ids: Sequence[str],
    created_review_request_ids: Sequence[str],
) -> list[str]:
    hints: list[str] = []
    git_refs = _optional_mapping(deliverable.get("git_refs"), "git_refs") or {}
    if git_refs.get("agent_branch") or git_refs.get("commit_sha"):
        hints.append(
            "Deliverable git_refs are provenance only; they do not publish a "
            "review branch by themselves."
        )
        hints.append(
            f"Call team_publish_review_branch with deliverable_id {deliverable_id!r} "
            "only when a durable review branch is needed for reviewer access, "
            "feature promotion, release sequencing, or an explicit superior request."
        )
    if created_review_request_ids:
        hints.append(
            "Review requests were created; wait for assigned reviewers to decide "
            "them before treating the task as accepted."
        )
    elif created_inbox_ids:
        hints.append(
            "The task is waiting_for_review for superior acceptance because a "
            "deliverable notice was sent to your superior. Wait for acceptance "
            "or branch-promotion instructions before taking more completion actions."
        )
    else:
        hints.append(
            "No direct-superior notice was created. Follow configured workflow or "
            "operator policy and team-specific review-routing guidance; do not infer "
            "reviewers without explicit manual-routing guidance."
        )
    verification = _optional_mapping(deliverable.get("verification"), "verification")
    if verification is not None and verification.get("status") == "not_run":
        hints.append(
            "Verification was recorded as not_run; keep the reason in "
            "verification.summary so reviewers can decide whether more validation "
            "is required."
        )
    return hints


def _validate_deliverable_outcomes_against_task_contract(
    task: AgentTask,
    outcomes: Sequence[DeliverableOutcome],
    *,
    completion: TaskCompletion | None = None,
) -> None:
    contract = task.outcome_contract
    if completion is not None:
        if task.task_kind != TeamTaskKind.OUTCOME or contract is None:
            raise ValueError("evidenced-noop completion is only valid for an outcome task")
        if completion.kind != TaskCompletionKind.EVIDENCED_NOOP:
            raise ValueError("deliverable completion currently supports evidenced_noop only")
        if not contract.allow_evidenced_noop:
            raise ValueError(f"outcome task {task.id} does not allow an evidenced no-op")
        if outcomes:
            raise ValueError("evidenced-noop deliverable cannot also declare delivered outcomes")
        return
    if contract is None:
        return

    outcome_kinds = {outcome.kind for outcome in outcomes}
    forbidden = sorted(outcome_kinds.intersection(contract.forbidden_outcome_kinds))
    if forbidden:
        raise ValueError("deliverable includes forbidden outcome kinds: " + ", ".join(forbidden))

    required = set(contract.required_outcome_kinds)
    satisfied = (
        bool(required.intersection(outcome_kinds))
        if contract.match == "any"
        else required.issubset(outcome_kinds)
    )
    if required and not satisfied:
        raise ValueError(
            "deliverable does not satisfy task outcome contract; required "
            f"{contract.match}: {', '.join(sorted(required))}"
        )

    missing_references = sorted({outcome.kind for outcome in outcomes if not outcome.reference_ids})
    if missing_references:
        raise ValueError(
            "task deliverable outcomes must include reference_ids: " + ", ".join(missing_references)
        )
    if not outcomes:
        raise ValueError(
            "outcome task deliverables must include an outcome with reference_ids; "
            "use an evidenced_noop task completion when the contract allows an "
            "evidence-backed no-op"
        )


def _superseded_deliverable_for_rework(
    state: TeamState,
    *,
    task: AgentTask,
    producer_agent_id: str,
    supersedes_deliverable_id: str | None,
) -> Deliverable | None:
    if supersedes_deliverable_id is None:
        return None
    deliverable = state.deliverables.get(supersedes_deliverable_id)
    if deliverable is None:
        raise ValueError(
            f"supersedes_deliverable_id references missing deliverable {supersedes_deliverable_id}"
        )
    if deliverable.task_id != task.id:
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} belongs to task "
            f"{deliverable.task_id}, not {task.id}"
        )
    if (
        deliverable.producer_agent_id != producer_agent_id
        and task.owner_agent_id != producer_agent_id
    ):
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} was produced by "
            f"{deliverable.producer_agent_id}; only that producer or current "
            f"task owner {task.owner_agent_id} may supersede it"
        )
    if deliverable.review_cohort_id is not None and deliverable.review_cohort_sealed_at is not None:
        unresolved_cohort_review_ids = sorted(
            review.id
            for review_id in deliverable.review_request_ids
            if (review := state.review_requests.get(review_id)) is not None
            and review.review_cohort_id == deliverable.review_cohort_id
            and review.superseded_by_review_request_id is None
            and review.retired_at is None
            and review.state
            in {
                TeamRecordState.PENDING,
                TeamRecordState.IN_PROGRESS,
                TeamRecordState.BLOCKED,
            }
        )
        if unresolved_cohort_review_ids:
            raise ValueError(
                f"supersedes_deliverable_id {deliverable.id} belongs to sealed "
                f"review cohort {deliverable.review_cohort_id} with unresolved "
                "review slots; complete or retry the cohort before replacing "
                f"the deliverable: {', '.join(unresolved_cohort_review_ids)}"
            )
    if deliverable.state not in {
        TeamRecordState.REJECTED,
        TeamRecordState.BLOCKED,
    } and not (
        deliverable.state == TeamRecordState.ACCEPTED
        and deliverable.rework_requested_at is not None
    ):
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} is not rejected, blocked, "
            "or explicitly reopened for rework"
        )
    if deliverable.superseded_by_deliverable_id is not None:
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} is already superseded by "
            f"{deliverable.superseded_by_deliverable_id}"
        )
    return deliverable


def _open_accepted_rework_deliverables(
    state: TeamState,
    *,
    task: AgentTask,
) -> list[Deliverable]:
    return sorted(
        (
            deliverable
            for deliverable_id in task.deliverable_ids
            if (deliverable := state.deliverables.get(deliverable_id)) is not None
            and deliverable.state == TeamRecordState.ACCEPTED
            and deliverable.rework_requested_at is not None
            and deliverable.superseded_by_deliverable_id is None
        ),
        key=lambda deliverable: (deliverable.rework_requested_at, deliverable.id),
    )


def _active_task_deliverables(
    state: TeamState,
    task: AgentTask,
) -> list[Deliverable]:
    return [
        deliverable
        for deliverable_id in task.deliverable_ids
        if (deliverable := state.deliverables.get(deliverable_id)) is not None
        and deliverable.superseded_by_deliverable_id is None
    ]


def _active_deliverable_acceptance_blocker_ids(
    state: TeamState,
    *,
    deliverable: Deliverable,
    task: AgentTask,
) -> list[str]:
    """Return blockers that prevent accepting a deliverable and its task."""

    return sorted(
        blocker.id
        for blocker in state.blockers.values()
        if blocker.state == TeamBlockerState.OPEN
        and (
            blocker.task_id == task.id
            or (
                blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE
                and blocker.source_id == deliverable.id
            )
        )
    )


def _accept_deliverable_and_maybe_task(
    state: TeamState,
    *,
    deliverable: Deliverable,
    task: AgentTask,
    now: datetime,
    result: dict[str, Any] | None = None,
    accepted_by_agent_id: str | None = None,
) -> bool:
    """Accept one deliverable and close its task when every active product is accepted."""

    deliverable.state = TeamRecordState.ACCEPTED
    task_deliverables = _active_task_deliverables(state, task)
    if not task_deliverables or not all(
        item.state == TeamRecordState.ACCEPTED for item in task_deliverables
    ):
        return False
    if _open_accepted_rework_deliverables(state, task=task):
        return False
    if task.task_kind == TeamTaskKind.OUTCOME:
        noop_completions = [
            item.completion for item in task_deliverables if item.completion is not None
        ]
        if noop_completions:
            if len(noop_completions) != len(task_deliverables):
                raise ValueError(
                    "an outcome task cannot mix evidenced-noop and delivered "
                    "products in one accepted lifecycle"
                )
            task.completion = noop_completions[0].model_copy(deep=True)
        else:
            outcome_reference_ids = [
                reference_id
                for item in task_deliverables
                for outcome in item.outcomes
                for reference_id in outcome.reference_ids
            ]
            task.completion = TaskCompletion(
                kind=TaskCompletionKind.DELIVERED,
                summary="Accepted deliverables satisfied the task outcome contract.",
                outcome_reference_ids=outcome_reference_ids,
            )
        next_state = TeamTaskState.ACCEPTED
    else:
        next_state = TeamTaskState.FINISHED
    _set_task_state(task, next_state, now=now)
    finalize_active_git_work_contexts_for_task(
        state,
        task_id=task.id,
        now=now,
    )
    task.updated_at = now
    from .dependency_composition_recovery import (
        accept_dependency_composition_recovery,
    )

    accept_dependency_composition_recovery(
        state,
        task=task,
        deliverable=deliverable,
        accepted_by_agent_id=accepted_by_agent_id,
        dispatch_id=None,
        result=result,
        now=now,
    )
    if task.parent_task_id is None:
        for accepted_deliverable in task_deliverables:
            initialize_pending_initial_release_disposition(
                state,
                task=task,
                deliverable=accepted_deliverable,
                now=now,
            )
    parent_notices = queue_parent_resolution_transitions(
        state,
        child_task_id=task.id,
        sender_agent_id=deliverable.producer_agent_id,
        result=result,
        now=now,
        references={"deliverable_id": deliverable.id},
    )
    if not parent_notices and task.parent_task_id is None:
        queue_root_task_resolution(
            state,
            task_id=task.id,
            deliverable_id=deliverable.id,
            sender_agent_id=accepted_by_agent_id or deliverable.producer_agent_id,
            result=result,
            now=now,
        )
    return True


__all__ = [
    "_DELIVERABLE_VERIFICATION_STATUSES",
    "_deliverable_git_refs_from_payload",
    "_normalize_deliverable_verification_mapping",
    "_deliverable_verification_from_payload",
    "_deliverable_verification_payload",
    "_deliverable_submission_next_action_hints",
    "_validate_deliverable_outcomes_against_task_contract",
    "_superseded_deliverable_for_rework",
    "_open_accepted_rework_deliverables",
    "_active_task_deliverables",
    "_active_deliverable_acceptance_blocker_ids",
    "_accept_deliverable_and_maybe_task",
]
