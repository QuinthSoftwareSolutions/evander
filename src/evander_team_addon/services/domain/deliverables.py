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
) -> None:
    contract = task.outcome_contract
    if contract is None:
        return

    outcome_kinds = {outcome.kind for outcome in outcomes}
    forbidden = sorted(
        outcome_kinds.intersection(contract.forbidden_outcome_kinds)
    )
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
    if deliverable.state not in {
        TeamRecordState.REJECTED,
        TeamRecordState.BLOCKED,
    }:
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} is not rejected or blocked"
        )
    if deliverable.superseded_by_deliverable_id is not None:
        raise ValueError(
            f"supersedes_deliverable_id {deliverable.id} is already superseded by "
            f"{deliverable.superseded_by_deliverable_id}"
        )
    return deliverable


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


def _accept_deliverable_and_maybe_task(
    state: TeamState,
    *,
    deliverable: Deliverable,
    task: AgentTask,
    now: datetime,
) -> bool:
    """Accept one deliverable and close its task when every active product is accepted."""

    deliverable.state = TeamRecordState.ACCEPTED
    task_deliverables = _active_task_deliverables(state, task)
    if not task_deliverables or not all(
        item.state == TeamRecordState.ACCEPTED for item in task_deliverables
    ):
        return False
    if task.task_kind == TeamTaskKind.OUTCOME:
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
    _set_task_state(task, TeamTaskState.ACCEPTED, now=now)
    task.updated_at = now
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
    "_active_task_deliverables",
    "_accept_deliverable_and_maybe_task",
]
