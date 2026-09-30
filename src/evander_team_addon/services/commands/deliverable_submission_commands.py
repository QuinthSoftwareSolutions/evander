from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any

from evander.models import utc_now
from evander.review_snapshots import team_deliverable_snapshot_ref

from .deliverable_helpers import (
    _apply_command_deliverable_submission,
    _command_deliverable_id,
    _command_deliverable_task_for_submission,
    _normalize_deliverable_submission_mapping,
    _normalize_review_request_mapping,
    _plan_command_review_requests_for_deliverable,
    _publish_command_deliverable_artifacts,
    _team_submit_deliverable_for_review_normalized_payload,
    _team_submit_deliverable_normalized_payload,
    _validate_command_deliverable_submission_pre_publication,
)
from .executor import TeamCommandExecutor
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    TeamDeliverableArtifactPublisher,
    _ResolvedTeamCommandContext,
)
from .validators import _require_command_agent
from ...models import (
    Deliverable,
    DeliverableAcceptanceDecision,
    TeamBlockerSourceKind,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    _unique_text_list,
    deliverable_requires_formal_review,
)
from ..domain.common import (
    _optional_text,
    _required_text,
    _set_task_state,
)
from ..domain.agent_access import _is_project_lead_for, _is_team_manager
from ..domain.blockers import resolve_active_blockers_for_source
from ..domain.deliverables import (
    _accept_deliverable_and_maybe_task,
    _active_deliverable_acceptance_blocker_ids,
    _deliverable_submission_next_action_hints,
    _validate_deliverable_outcomes_against_task_contract,
)
from ..domain.project_status import _recalculate_project_status
from ..domain.review import (
    _apply_review_request,
    _review_request_was_created,
)
from ..domain.review_handoff import queue_actionable_inbox_item
from ..domain.task_assessment import (
    TaskAssessmentBoundaryError,
    assess_task,
    require_deliverable_acceptance,
    task_payload_with_assessment,
)
from ..domain.task_supervision import queue_parent_task_transition
from ..domain.work_due import (
    _mark_agent_work_due,
)
from ..domain.wake import _append_agent_wake_requested_event_for_inbox_item
from ..domain.dependency_composition_recovery import (
    verify_dependency_recovery_submission,
)


def _with_server_pinned_git_candidate(
    state: TeamState,
    *,
    task: Any,
    deliverable: Mapping[str, Any],
    deliverable_id: str,
    candidate_snapshot_pinner: Any | None,
) -> dict[str, Any]:
    """Pin a submitted Git candidate before its mutable agent branch can move."""

    payload = dict(deliverable)
    git_refs = dict(payload.get("git_refs") or {})
    commit_sha = _optional_text(git_refs.get("commit_sha"))
    project = state.projects.get(task.project_id)
    if commit_sha is None or project is None or project.git is None:
        return payload

    pinned_ref = team_deliverable_snapshot_ref(deliverable_id)
    if candidate_snapshot_pinner is not None:
        try:
            result = candidate_snapshot_pinner(
                managed_repo_id=project.git.managed_repo_id,
                directory_path=project.git.directory_path,
                deliverable_id=deliverable_id,
                commit_sha=commit_sha,
                source_ref=_optional_text(git_refs.get("agent_branch")),
                expected_pinned_ref=pinned_ref,
            )
        except Exception as exc:
            raise TeamCommandValidationError(
                code="git_candidate_snapshot_pin_failed",
                message=(
                    f"could not retain immutable Git candidate {commit_sha} for "
                    f"deliverable {deliverable_id}: {exc}"
                ),
                correction_context={
                    "deliverable_id": deliverable_id,
                    "commit_sha": commit_sha,
                    "expected_pinned_ref": pinned_ref,
                },
            ) from None
        actual_ref = _optional_text(result.get("pin_ref"))
        actual_commit = _optional_text(result.get("commit_sha"))
        if actual_ref != pinned_ref or actual_commit is None or (
            actual_commit.casefold() != commit_sha.casefold()
        ):
            raise TeamCommandValidationError(
                code="git_candidate_snapshot_pin_mismatch",
                message=(
                    f"immutable Git candidate pin for deliverable {deliverable_id} "
                    "returned different provenance"
                ),
                correction_context={
                    "deliverable_id": deliverable_id,
                    "commit_sha": commit_sha,
                    "expected_pinned_ref": pinned_ref,
                    "actual_commit_sha": actual_commit,
                    "actual_pinned_ref": actual_ref,
                },
            )
    git_refs["accepted_pinned_ref"] = pinned_ref
    payload["git_refs"] = git_refs
    return payload


def _require_predecessor_review_coverage(
    state: TeamState,
    *,
    task_id: str,
    predecessor: Deliverable | None,
    planned_reviews: Sequence[Mapping[str, Any]],
) -> None:
    if predecessor is None:
        return
    if predecessor.review_cohort_id is not None and predecessor.review_cohort_sealed_at is not None:
        predecessor_slots = [
            review
            for review_id in predecessor.review_request_ids
            if (review := state.review_requests.get(review_id)) is not None
            and review.review_cohort_id == predecessor.review_cohort_id
            and review.superseded_by_review_request_id is None
            and review.retired_at is None
        ]
        required_profile_counts: dict[str, int] = {}
        required_unprofiled_reviewer_counts: dict[str, int] = {}
        for review in predecessor_slots:
            if review.review_profile_id is not None:
                required_profile_counts[review.review_profile_id] = (
                    required_profile_counts.get(review.review_profile_id, 0) + 1
                )
            else:
                required_unprofiled_reviewer_counts[review.reviewer_agent_id] = (
                    required_unprofiled_reviewer_counts.get(
                        review.reviewer_agent_id,
                        0,
                    )
                    + 1
                )
        planned_profile_counts: dict[str, int] = {}
        planned_unprofiled_reviewer_counts: dict[str, int] = {}
        for review in planned_reviews:
            profile_id = _optional_text(review.get("review_profile_id"))
            reviewer_id = _required_text(
                review.get("reviewer_agent_id"),
                "reviewer_agent_id",
            )
            if profile_id is not None:
                planned_profile_counts[profile_id] = planned_profile_counts.get(profile_id, 0) + 1
            else:
                planned_unprofiled_reviewer_counts[reviewer_id] = (
                    planned_unprofiled_reviewer_counts.get(reviewer_id, 0) + 1
                )
        missing_profile_counts = {
            profile_id: required_count - planned_profile_counts.get(profile_id, 0)
            for profile_id, required_count in required_profile_counts.items()
            if planned_profile_counts.get(profile_id, 0) < required_count
        }
        missing_unprofiled_reviewer_counts = {
            reviewer_id: (required_count - planned_unprofiled_reviewer_counts.get(reviewer_id, 0))
            for reviewer_id, required_count in required_unprofiled_reviewer_counts.items()
            if (planned_unprofiled_reviewer_counts.get(reviewer_id, 0) < required_count)
        }
        if missing_profile_counts or missing_unprofiled_reviewer_counts:
            error_code = (
                "accepted_deliverable_rework_review_coverage_incomplete"
                if (
                    predecessor.state == TeamRecordState.ACCEPTED
                    and predecessor.rework_requested_at is not None
                )
                else "sealed_review_cohort_replacement_coverage_incomplete"
            )
            raise TeamCommandValidationError(
                code=error_code,
                message=(
                    f"replacement for sealed-cohort deliverable {predecessor.id} "
                    "must preserve every current review slot"
                ),
                correction_context={
                    "task_id": task_id,
                    "supersedes_deliverable_id": predecessor.id,
                    "review_cohort_id": predecessor.review_cohort_id,
                    "required_review_profile_counts": required_profile_counts,
                    "planned_review_profile_counts": planned_profile_counts,
                    "missing_review_profile_counts": missing_profile_counts,
                    "required_unprofiled_reviewer_counts": (required_unprofiled_reviewer_counts),
                    "planned_unprofiled_reviewer_counts": (planned_unprofiled_reviewer_counts),
                    "missing_unprofiled_reviewer_counts": (missing_unprofiled_reviewer_counts),
                    # Preserve the legacy accepted-rework correction shape.
                    "required_review_profile_ids": sorted(required_profile_counts),
                    "missing_review_profile_ids": sorted(missing_profile_counts),
                    "missing_unprofiled_reviewer_agent_ids": sorted(
                        missing_unprofiled_reviewer_counts
                    ),
                },
            )
        return
    if not (
        predecessor.state == TeamRecordState.ACCEPTED
        and predecessor.rework_requested_at is not None
    ):
        return
    required_review_profile_ids = sorted(
        {
            review.review_profile_id
            for review_id in predecessor.review_request_ids
            if (review := state.review_requests.get(review_id)) is not None
            and review.state == TeamRecordState.ACCEPTED
            and review.review_profile_id is not None
            and review.superseded_by_review_request_id is None
            and review.retired_at is None
        }
    )
    planned_review_profile_ids = {
        _optional_text(review.get("review_profile_id")) for review in planned_reviews
    }
    missing_review_profile_ids = sorted(
        set(required_review_profile_ids) - planned_review_profile_ids
    )
    if missing_review_profile_ids:
        raise TeamCommandValidationError(
            code="accepted_deliverable_rework_review_coverage_incomplete",
            message=(
                f"replacement for accepted deliverable {predecessor.id} "
                "must repeat every current required review profile"
            ),
            correction_context={
                "task_id": task_id,
                "supersedes_deliverable_id": predecessor.id,
                "required_review_profile_ids": required_review_profile_ids,
                "missing_review_profile_ids": missing_review_profile_ids,
            },
        )


class TeamDeliverableSubmissionCommandService(TeamCommandExecutor):
    def submit_deliverable_command(
        self,
        *,
        session_id: str,
        task_id: str,
        summary: str,
        request_id: str,
        id: str | None = None,
        supersedes_deliverable_id: str | None = None,
        artifact_paths: Sequence[str] | None = None,
        artifact_refs: Sequence[str] | None = None,
        artifact_space_ref: str | None = None,
        branch_context_id: str | None = None,
        git_refs: Mapping[str, Any] | None = None,
        verification: Mapping[str, Any] | None = None,
        outcomes: Sequence[Mapping[str, Any]] | None = None,
        source_deliverable_ids: Sequence[str] | None = None,
        completion: Mapping[str, Any] | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        artifact_publisher: TeamDeliverableArtifactPublisher | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_deliverable = _normalize_deliverable_submission_mapping(
            {
                "id": id,
                "task_id": task_id,
                "summary": summary,
                "supersedes_deliverable_id": supersedes_deliverable_id,
                "artifact_paths": list(artifact_paths or []),
                "artifact_refs": list(artifact_refs or []),
                "artifact_space_ref": artifact_space_ref,
                "branch_context_id": branch_context_id,
                "git_refs": dict(git_refs or {}),
                "verification": dict(verification) if verification is not None else None,
                "outcomes": [dict(item) for item in outcomes or []],
                "source_deliverable_ids": list(source_deliverable_ids or []),
                "completion": dict(completion) if completion is not None else None,
            },
            field_name="deliverable",
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_submit_deliverable",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="deliverable",
        )
        normalized_payload = _team_submit_deliverable_normalized_payload(
            deliverable=normalized_deliverable,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_submit_deliverable")
            task = _command_deliverable_task_for_submission(
                state,
                agent=agent,
                task_id=normalized_deliverable["task_id"],
            )
            deliverable_id = _command_deliverable_id(
                state,
                deliverable=normalized_deliverable,
                request_id=normalized_request_id,
            )
            _validate_command_deliverable_submission_pre_publication(
                state,
                agent=agent,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
                dispatch_id=resolved.dispatch_id,
                candidate_commit_resolver=self.git_candidate_commit_resolver,
            )
            try:
                recovery_git_refs = dict(normalized_deliverable.get("git_refs") or {})
                verify_dependency_recovery_submission(
                    state,
                    task_id=task.id,
                    deliverable_id=deliverable_id,
                    branch=_optional_text(recovery_git_refs.get("agent_branch")) or "",
                    commit_sha=_optional_text(recovery_git_refs.get("commit_sha")) or "",
                    source_deliverable_ids=normalized_deliverable["source_deliverable_ids"],
                    verifier=self.dependency_recovery_commit_verifier,
                    now=utc_now(),
                )
            except ValueError as exc:
                raise TeamCommandValidationError(
                    code="dependency_composition_recovery_verification_failed",
                    message=str(exc),
                    correction_context={
                        "task_id": task.id,
                        "deliverable_id": deliverable_id,
                    },
                ) from None
            predecessor_id = _optional_text(normalized_deliverable.get("supersedes_deliverable_id"))
            predecessor = state.deliverables.get(predecessor_id or "")
            if (
                predecessor is not None
                and predecessor.state == TeamRecordState.ACCEPTED
                and predecessor.rework_requested_at is not None
            ):
                raise TeamCommandValidationError(
                    code="accepted_deliverable_rework_requires_fresh_reviews",
                    message=(
                        f"replacement for accepted deliverable {predecessor.id} "
                        "must be submitted with fresh formal reviews"
                    ),
                    correction_context={
                        "task_id": task.id,
                        "supersedes_deliverable_id": predecessor.id,
                        "recommended_tool": "team_submit_deliverable_for_review",
                    },
                )
            submission_deliverable = _with_server_pinned_git_candidate(
                state,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
                candidate_snapshot_pinner=self.git_candidate_snapshot_pinner,
            )
            published_records = _publish_command_deliverable_artifacts(
                state,
                agent=agent,
                task=task,
                deliverable_id=deliverable_id,
                dispatch_id=resolved.dispatch_id or "manual",
                artifact_paths=normalized_deliverable["artifact_paths"],
                artifact_publisher=artifact_publisher,
            )
            created_inbox_ids = _apply_command_deliverable_submission(
                state,
                agent=agent,
                task=task,
                deliverable=submission_deliverable,
                deliverable_id=deliverable_id,
                published_records=published_records,
                suppress_superior_notice=False,
                context_snapshot_ids=self._repository.snapshots.ids_for_dispatch(
                    state.agent_runtimes[agent.id].active_dispatch_id, state=state
                ),
            )
            submission_events: dict[str, Any] = {"events": []}
            for inbox_id in created_inbox_ids:
                inbox_item = state.inbox.get(inbox_id)
                if inbox_item is None:
                    continue
                _append_agent_wake_requested_event_for_inbox_item(
                    state,
                    item=inbox_item,
                    result=submission_events,
                    now=inbox_item.created_at,
                )
            published_refs = [
                _required_text(record.get("artifact_ref"), "artifact_ref")
                for record in published_records
            ]
            return TeamCommandApplyResult(
                response_payload={
                    "deliverable_id": deliverable_id,
                    "created_deliverable_ids": [deliverable_id],
                    "created_inbox_item_ids": created_inbox_ids,
                    "events": list(submission_events["events"]),
                    "published_artifact_refs": published_refs,
                    "published_deliverable_artifacts": [
                        dict(record) for record in published_records
                    ],
                    "task_id": task.id,
                    "task_state": state.tasks[task.id].state.value,
                    "waiting_reason": task_payload_with_assessment(state, task)["waiting_reason"],
                    "intent_recorded": True,
                    "async_effect_pending": bool(created_inbox_ids),
                    "verification": normalized_deliverable.get("verification"),
                    "next_action_hints": _deliverable_submission_next_action_hints(
                        deliverable_id=deliverable_id,
                        deliverable=normalized_deliverable,
                        created_inbox_ids=created_inbox_ids,
                        created_review_request_ids=[],
                    ),
                    "progress_made": True,
                },
                created_domain_ids=[deliverable_id, *created_inbox_ids],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
            reject_stale_source_evidence=True,
        )

    def decide_deliverable_command(
        self,
        *,
        session_id: str,
        deliverable_id: str,
        decision: str,
        request_id: str,
        reason: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_decision = _required_text(decision, "decision").lower()
        if normalized_decision not in {"accepted", "rejected"}:
            raise ValueError("decision must be accepted or rejected")
        normalized_reason = _optional_text(reason)
        if normalized_decision == "rejected" and normalized_reason is None:
            raise ValueError("reason is required when decision is rejected")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        normalized_payload = {
            "deliverable_id": _required_text(deliverable_id, "deliverable_id"),
            "decision": normalized_decision,
            "reason": normalized_reason,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": _optional_text(correlation_id),
            "causation_id": _optional_text(causation_id),
        }
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_decide_deliverable",
            request_id=normalized_request_id,
            correlation_id=normalized_payload["correlation_id"],
            causation_id=normalized_payload["causation_id"],
            progress_classification="deliverable_decision",
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_decide_deliverable")
            deliverable = state.deliverables.get(normalized_payload["deliverable_id"])
            if deliverable is None:
                raise TeamCommandValidationError(
                    code="deliverable_not_found",
                    message=(f"deliverable {normalized_payload['deliverable_id']} does not exist"),
                )
            task = state.tasks.get(deliverable.task_id)
            if task is None:
                raise TeamCommandValidationError(
                    code="deliverable_task_not_found",
                    message=f"deliverable {deliverable.id} references a missing task",
                )
            acceptance_recipient_agent_id = deliverable.acceptance_recipient_agent_id
            if acceptance_recipient_agent_id is None:
                legacy_notice_recipients = {
                    item.recipient_agent_id
                    for item in state.inbox.values()
                    if item.item_type == TeamInboxItemType.DELIVERABLE_NOTICE
                    and item.payload.get("deliverable_id") == deliverable.id
                }
                if len(legacy_notice_recipients) == 1:
                    acceptance_recipient_agent_id = next(iter(legacy_notice_recipients))
            if acceptance_recipient_agent_id != agent.id:
                raise TeamCommandValidationError(
                    code="deliverable_decision_forbidden",
                    message=(
                        f"deliverable {deliverable.id} was handed to "
                        f"{acceptance_recipient_agent_id or 'no superior'}; "
                        f"agent {agent.id} cannot decide it"
                    ),
                    correction_context={
                        "acceptance_recipient_agent_id": acceptance_recipient_agent_id,
                    },
                )
            if deliverable.acceptance_decision is not None:
                raise TeamCommandValidationError(
                    code="deliverable_already_decided",
                    message=f"deliverable {deliverable.id} already has a decision",
                    correction_context={
                        "decision": deliverable.acceptance_decision.decision,
                        "decided_by_agent_id": (
                            deliverable.acceptance_decision.decided_by_agent_id
                        ),
                    },
                )
            if deliverable.superseded_by_deliverable_id is not None:
                raise TeamCommandValidationError(
                    code="deliverable_superseded",
                    message=(
                        f"deliverable {deliverable.id} was superseded by "
                        f"{deliverable.superseded_by_deliverable_id}"
                    ),
                )
            if deliverable_requires_formal_review(deliverable, state=state):
                raise TeamCommandValidationError(
                    code="formal_review_required",
                    message=(
                        f"deliverable {deliverable.id} has a formal review path; "
                        "decide its assigned review requests instead"
                    ),
                    correction_context={
                        "review_request_ids": list(deliverable.review_request_ids),
                        "recommended_tool": "team_decide_review",
                    },
                )
            waiting_reason = task_payload_with_assessment(state, task)["waiting_reason"]
            if (
                deliverable.state != TeamRecordState.PENDING
                or task.state != TeamTaskState.WAITING_FOR_REVIEW
            ):
                raise TeamCommandValidationError(
                    code="deliverable_not_awaiting_superior",
                    message=(
                        f"deliverable {deliverable.id} is not awaiting direct superior acceptance"
                    ),
                    correction_context={
                        "deliverable_state": deliverable.state.value,
                        "task_state": task.state.value,
                        "waiting_reason": waiting_reason,
                    },
                )

            now = utc_now()
            if deliverable.acceptance_recipient_agent_id is None:
                deliverable.acceptance_recipient_agent_id = acceptance_recipient_agent_id
            created_inbox_ids: list[str] = []
            side_effects: dict[str, Any] = {"created_inbox_item_ids": created_inbox_ids}
            if normalized_decision == "accepted":
                try:
                    require_deliverable_acceptance(state, deliverable.id)
                except TaskAssessmentBoundaryError as exc:
                    raise TeamCommandValidationError(
                        code=exc.code,
                        message=exc.message,
                        correction_context=exc.correction_context,
                    ) from None
                active_blocker_ids = _active_deliverable_acceptance_blocker_ids(
                    state,
                    deliverable=deliverable,
                    task=task,
                )
                if active_blocker_ids:
                    raise TeamCommandValidationError(
                        code="deliverable_acceptance_blocked",
                        message=(
                            f"deliverable {deliverable.id} cannot be accepted while "
                            "its task has active blockers"
                        ),
                        correction_context={"active_blocker_ids": active_blocker_ids},
                    )
                try:
                    _validate_deliverable_outcomes_against_task_contract(
                        task,
                        deliverable.outcomes,
                        completion=deliverable.completion,
                    )
                except ValueError as exc:
                    raise TeamCommandValidationError(
                        code="deliverable_outcome_contract_failed",
                        message=str(exc),
                        correction_context={
                            "deliverable_id": deliverable.id,
                            "task_id": task.id,
                        },
                    ) from None
                task_accepted = _accept_deliverable_and_maybe_task(
                    state,
                    deliverable=deliverable,
                    task=task,
                    now=now,
                    result=side_effects,
                    accepted_by_agent_id=agent.id,
                )
            else:
                deliverable.state = TeamRecordState.REJECTED
                remaining_assessment = assess_task(state, task.id)
                _set_task_state(
                    task,
                    (
                        TeamTaskState.WAITING_FOR_REVIEW
                        if (
                            remaining_assessment.active_acceptance_ids
                            or remaining_assessment.active_review_request_ids
                        )
                        else TeamTaskState.IN_PROGRESS
                    ),
                    now=now,
                )
                task.updated_at = now
                task_accepted = False
                queue_actionable_inbox_item(
                    state,
                    recipient_agent_id=task.owner_agent_id,
                    sender_agent_id=agent.id,
                    task_id=task.id,
                    priority=TeamInboxPriority.NORMAL,
                    payload={
                        "kind": "superior_acceptance_rejected",
                        "deliverable_id": deliverable.id,
                        "supersedes_deliverable_id": deliverable.id,
                        "task_id": task.id,
                        "summary": "Superior rejected the deliverable; revise and resubmit.",
                        "reason": normalized_reason,
                        "artifact_refs": list(deliverable.artifact_refs),
                    },
                    result=side_effects,
                    now=now,
                )
                queue_parent_task_transition(
                    state,
                    child_task_id=task.id,
                    transition="rejection",
                    sender_agent_id=agent.id,
                    result=side_effects,
                    now=now,
                    references={"deliverable_id": deliverable.id},
                )
                resolve_active_blockers_for_source(
                    state,
                    source_kind=TeamBlockerSourceKind.DELIVERABLE,
                    source_id=deliverable.id,
                    actor_agent_id=agent.id,
                    dispatch_id=resolved.dispatch_id,
                    note="Superior rejected the deliverable for rework.",
                    reference_ids=[deliverable.id],
                    result=side_effects,
                    now=now,
                )

            deliverable.acceptance_decision = DeliverableAcceptanceDecision(
                decision=normalized_decision,
                decided_by_agent_id=agent.id,
                decided_at=now,
                reason=normalized_reason,
            )
            automatically_handled_inbox_item_ids = sorted(
                item.id
                for item in state.inbox.values()
                if item.id not in normalized_handled_ids
                and item.state == TeamInboxItemState.PENDING
                and item.recipient_agent_id == agent.id
                and item.item_type == TeamInboxItemType.DELIVERABLE_NOTICE
                and item.payload.get("deliverable_id") == deliverable.id
            )
            for inbox_item_id in automatically_handled_inbox_item_ids:
                item = state.inbox[inbox_item_id]
                item.state = TeamInboxItemState.HANDLED
                item.updated_at = now
            _recalculate_project_status(state, task.project_id)
            return TeamCommandApplyResult(
                response_payload={
                    "deliverable_id": deliverable.id,
                    "decision": normalized_decision,
                    "reason": normalized_reason,
                    "deliverable_state": deliverable.state.value,
                    "task_id": task.id,
                    "task_state": task.state.value,
                    "task_accepted": task_accepted,
                    "created_inbox_item_ids": list(
                        side_effects.get("created_inbox_item_ids") or []
                    ),
                    "events": list(side_effects.get("events") or []),
                    "resolved_blocker_ids": list(side_effects.get("resolved_blocker_ids") or []),
                    "automatically_handled_inbox_item_ids": (automatically_handled_inbox_item_ids),
                    "intent_recorded": True,
                    "async_effect_pending": bool(side_effects.get("created_inbox_item_ids")),
                    "progress_made": True,
                },
                created_domain_ids=list(side_effects.get("created_inbox_item_ids") or []),
                progress_classification="deliverable_decision",
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
            reject_stale_source_evidence=(normalized_decision == "accepted"),
        )

    def submit_deliverable_for_review_command(
        self,
        *,
        session_id: str,
        deliverable: Mapping[str, Any],
        reviews: Sequence[Mapping[str, Any]],
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        artifact_publisher: TeamDeliverableArtifactPublisher | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_deliverable = _normalize_deliverable_submission_mapping(
            deliverable,
            field_name="deliverable",
        )
        normalized_reviews = [
            _normalize_review_request_mapping(item, field_name=f"reviews[{index}]")
            for index, item in enumerate(reviews)
        ]
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_submit_deliverable_for_review",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="deliverable_review_request",
        )
        normalized_payload = _team_submit_deliverable_for_review_normalized_payload(
            deliverable=normalized_deliverable,
            reviews=normalized_reviews,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(
                resolved,
                "team_submit_deliverable_for_review",
            )
            task = _command_deliverable_task_for_submission(
                state,
                agent=agent,
                task_id=normalized_deliverable["task_id"],
            )
            deliverable_id = _command_deliverable_id(
                state,
                deliverable=normalized_deliverable,
                request_id=normalized_request_id,
            )
            _validate_command_deliverable_submission_pre_publication(
                state,
                agent=agent,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
                dispatch_id=resolved.dispatch_id,
                candidate_commit_resolver=self.git_candidate_commit_resolver,
            )
            try:
                recovery_git_refs = dict(normalized_deliverable.get("git_refs") or {})
                verify_dependency_recovery_submission(
                    state,
                    task_id=task.id,
                    deliverable_id=deliverable_id,
                    branch=_optional_text(recovery_git_refs.get("agent_branch")) or "",
                    commit_sha=_optional_text(recovery_git_refs.get("commit_sha")) or "",
                    source_deliverable_ids=normalized_deliverable["source_deliverable_ids"],
                    verifier=self.dependency_recovery_commit_verifier,
                    now=utc_now(),
                )
            except ValueError as exc:
                raise TeamCommandValidationError(
                    code="dependency_composition_recovery_verification_failed",
                    message=str(exc),
                    correction_context={
                        "task_id": task.id,
                        "deliverable_id": deliverable_id,
                    },
                ) from None
            if not normalized_reviews:
                raise TeamCommandValidationError(
                    code="empty_reviews",
                    message="reviews must contain at least one review request",
                    correction_context={"field": "reviews"},
                )
            provisional_deliverable = Deliverable(
                id=deliverable_id,
                task_id=task.id,
                producer_agent_id=agent.id,
                summary=normalized_deliverable["summary"],
                outcomes=normalized_deliverable["outcomes"],
                source_deliverable_ids=normalized_deliverable["source_deliverable_ids"],
                completion=normalized_deliverable.get("completion"),
                supersedes_deliverable_id=normalized_deliverable.get("supersedes_deliverable_id"),
            )
            planned_reviews = _plan_command_review_requests_for_deliverable(
                state,
                actor=agent,
                deliverable=provisional_deliverable,
                task=task,
                reviews=normalized_reviews,
                request_id=normalized_request_id,
            )
            planned_reviewer_ids = [
                _required_text(
                    request_payload.get("reviewer_agent_id"),
                    "reviewer_agent_id",
                )
                for request_payload in planned_reviews
            ]
            duplicate_reviewer_ids = sorted(
                {
                    reviewer_id
                    for reviewer_id in planned_reviewer_ids
                    if planned_reviewer_ids.count(reviewer_id) > 1
                }
            )
            if duplicate_reviewer_ids:
                raise TeamCommandValidationError(
                    code="duplicate_review_cohort_members",
                    message=(
                        "a sealed review cohort requires one review slot per "
                        "reviewer; duplicate reviewers: " + ", ".join(duplicate_reviewer_ids)
                    ),
                    correction_context={
                        "deliverable_id": deliverable_id,
                        "duplicate_reviewer_agent_ids": duplicate_reviewer_ids,
                    },
                )
            delivered_outcome_kinds = {outcome.kind for outcome in provisional_deliverable.outcomes}
            for request_payload in planned_reviews:
                profile_id = _optional_text(request_payload.get("review_profile_id"))
                profile = state.review_profiles.get(profile_id or "")
                if profile is None or not profile.required_outcome_kinds:
                    continue
                missing_outcome_kinds = sorted(
                    set(profile.required_outcome_kinds) - delivered_outcome_kinds
                )
                if missing_outcome_kinds:
                    raise TeamCommandValidationError(
                        code="review_profile_outcome_requirements_unsatisfied",
                        message=(
                            f"review profile {profile.id} requires deliverable "
                            "outcome kinds: " + ", ".join(missing_outcome_kinds)
                        ),
                        correction_context={
                            "deliverable_id": deliverable_id,
                            "review_profile_id": profile.id,
                            "missing_outcome_kinds": missing_outcome_kinds,
                        },
                    )
            predecessor_id = _optional_text(normalized_deliverable.get("supersedes_deliverable_id"))
            predecessor = state.deliverables.get(predecessor_id or "")
            _require_predecessor_review_coverage(
                state,
                task_id=task.id,
                predecessor=predecessor,
                planned_reviews=planned_reviews,
            )
            submission_deliverable = _with_server_pinned_git_candidate(
                state,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
                candidate_snapshot_pinner=self.git_candidate_snapshot_pinner,
            )
            published_records = _publish_command_deliverable_artifacts(
                state,
                agent=agent,
                task=task,
                deliverable_id=deliverable_id,
                dispatch_id=resolved.dispatch_id or "manual",
                artifact_paths=normalized_deliverable["artifact_paths"],
                artifact_publisher=artifact_publisher,
            )
            _apply_command_deliverable_submission(
                state,
                agent=agent,
                task=task,
                deliverable=submission_deliverable,
                deliverable_id=deliverable_id,
                published_records=published_records,
                suppress_superior_notice=True,
                context_snapshot_ids=self._repository.snapshots.ids_for_dispatch(
                    state.agent_runtimes[agent.id].active_dispatch_id, state=state
                ),
            )
            submitted_deliverable = state.deliverables[deliverable_id]
            review_cohort_id = f"review-cohort-{deliverable_id}"
            submitted_deliverable.review_cohort_id = review_cohort_id
            review_result = {"errors": [], "created_inbox_item_ids": []}
            created_review_ids: list[str] = []
            for index, request_payload in enumerate(planned_reviews):
                created_id = _apply_review_request(
                    state,
                    actor=agent,
                    request={
                        **request_payload,
                        "review_cohort_id": review_cohort_id,
                    },
                    result=review_result,
                )
                if created_id is None:
                    raise TeamCommandValidationError(
                        code="review_request_failed",
                        message="; ".join(review_result["errors"])
                        or f"reviews[{index}] could not be created",
                        correction_context={
                            "deliverable_id": deliverable_id,
                            "review": dict(request_payload),
                            "errors": list(review_result["errors"]),
                        },
                    )
                if _review_request_was_created(review_result, created_id):
                    created_review_ids.append(created_id)
            submitted_deliverable.review_cohort_sealed_at = utc_now()
            for review_id in created_review_ids:
                review = state.review_requests[review_id]
                _mark_agent_work_due(
                    state,
                    agent_id=review.reviewer_agent_id,
                    now=utc_now(),
                )
            published_refs = [
                _required_text(record.get("artifact_ref"), "artifact_ref")
                for record in published_records
            ]
            created_inbox_ids = list(review_result["created_inbox_item_ids"])
            return TeamCommandApplyResult(
                response_payload={
                    "deliverable_id": deliverable_id,
                    "review_cohort_id": review_cohort_id,
                    "review_cohort_sealed_at": (
                        submitted_deliverable.review_cohort_sealed_at.isoformat()
                    ),
                    "created_deliverable_ids": [deliverable_id],
                    "created_review_request_ids": created_review_ids,
                    "existing_review_request_ids": list(
                        review_result.get("existing_review_request_ids", [])
                    ),
                    "created_inbox_item_ids": created_inbox_ids,
                    "events": list(review_result.get("events") or []),
                    "published_artifact_refs": published_refs,
                    "published_deliverable_artifacts": [
                        dict(record) for record in published_records
                    ],
                    "task_id": task.id,
                    "task_state": state.tasks[task.id].state.value,
                    "waiting_reason": task_payload_with_assessment(state, task)["waiting_reason"],
                    "intent_recorded": True,
                    "async_effect_pending": bool(
                        created_inbox_ids or review_result.get("events")
                    ),
                    "verification": (
                        submitted_deliverable.verification.model_dump(
                            mode="json",
                            exclude_none=True,
                        )
                        if submitted_deliverable.verification is not None
                        else None
                    ),
                    "next_action_hints": _deliverable_submission_next_action_hints(
                        deliverable_id=deliverable_id,
                        deliverable=normalized_deliverable,
                        created_inbox_ids=created_inbox_ids,
                        created_review_request_ids=created_review_ids,
                    ),
                    "progress_made": True,
                },
                created_domain_ids=[
                    deliverable_id,
                    *created_review_ids,
                    *created_inbox_ids,
                ],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
            reject_stale_source_evidence=True,
        )

    def link_deliverable_replacement_command(
        self,
        *,
        session_id: str,
        predecessor_deliverable_id: str,
        replacement_deliverable_id: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_payload = {
            "predecessor_deliverable_id": _required_text(
                predecessor_deliverable_id, "predecessor_deliverable_id"
            ),
            "replacement_deliverable_id": _required_text(
                replacement_deliverable_id, "replacement_deliverable_id"
            ),
            "reason": _required_text(reason, "reason"),
            "handled_inbox_item_ids": sorted(
                _unique_text_list(
                    handled_inbox_item_ids or [],
                    "handled_inbox_item_ids",
                )
            ),
            "correlation_id": _optional_text(correlation_id),
            "causation_id": _optional_text(causation_id),
        }
        normalized_handled_ids = list(normalized_payload["handled_inbox_item_ids"])
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_link_deliverable_replacement",
            request_id=normalized_request_id,
            correlation_id=normalized_payload["correlation_id"],
            causation_id=normalized_payload["causation_id"],
            progress_classification="deliverable_lineage",
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_link_deliverable_replacement")
            predecessor = state.deliverables.get(normalized_payload["predecessor_deliverable_id"])
            replacement = state.deliverables.get(normalized_payload["replacement_deliverable_id"])
            if predecessor is None or replacement is None:
                missing = (
                    normalized_payload["predecessor_deliverable_id"]
                    if predecessor is None
                    else normalized_payload["replacement_deliverable_id"]
                )
                raise TeamCommandValidationError(
                    code="deliverable_not_found",
                    message=f"deliverable {missing} does not exist",
                )
            predecessor_task = state.tasks.get(predecessor.task_id)
            replacement_task = state.tasks.get(replacement.task_id)
            if predecessor_task is None or replacement_task is None:
                raise TeamCommandValidationError(
                    code="deliverable_task_not_found",
                    message="both deliverables must reference existing tasks",
                )
            if predecessor_task.project_id != replacement_task.project_id:
                raise TeamCommandValidationError(
                    code="cross_project_deliverable_replacement",
                    message="deliverable replacements must remain within one project",
                )
            project_id = predecessor_task.project_id
            project = state.projects.get(project_id)
            if not (
                _is_project_lead_for(state, agent.id, project_id)
                or (
                    project is not None
                    and project.team_id == agent.team_id
                    and _is_team_manager(state, agent)
                )
            ):
                raise TeamCommandValidationError(
                    code="deliverable_replacement_forbidden",
                    message=(
                        f"agent {agent.id} must be the project lead or team manager "
                        f"to link a cross-lifecycle replacement"
                    ),
                )
            if predecessor.state not in {
                TeamRecordState.REJECTED,
                TeamRecordState.BLOCKED,
            }:
                raise TeamCommandValidationError(
                    code="predecessor_not_replaceable",
                    message="predecessor deliverable must be rejected or blocked",
                )
            if replacement.state != TeamRecordState.ACCEPTED:
                raise TeamCommandValidationError(
                    code="replacement_not_accepted",
                    message="replacement deliverable must be accepted",
                )
            if predecessor.superseded_by_deliverable_id is not None:
                raise TeamCommandValidationError(
                    code="predecessor_already_superseded",
                    message=(
                        f"predecessor is already superseded by "
                        f"{predecessor.superseded_by_deliverable_id}"
                    ),
                )
            if replacement.supersedes_deliverable_id is not None:
                raise TeamCommandValidationError(
                    code="replacement_lineage_already_set",
                    message=(
                        f"replacement already supersedes {replacement.supersedes_deliverable_id}"
                    ),
                )
            cursor = predecessor
            visited: set[str] = set()
            while cursor.supersedes_deliverable_id is not None:
                if cursor.id in visited:
                    raise TeamCommandValidationError(
                        code="deliverable_lineage_cycle",
                        message="existing deliverable lineage contains a cycle",
                    )
                visited.add(cursor.id)
                ancestor_id = cursor.supersedes_deliverable_id
                if ancestor_id == replacement.id:
                    raise TeamCommandValidationError(
                        code="deliverable_lineage_cycle",
                        message="replacement link would create a deliverable lineage cycle",
                    )
                ancestor = state.deliverables.get(ancestor_id)
                if ancestor is None:
                    break
                cursor = ancestor
            now = utc_now()
            predecessor.superseded_by_deliverable_id = replacement.id
            predecessor.supersession_reason = normalized_payload["reason"]
            predecessor.superseded_at = now
            predecessor.superseded_by_actor = f"agent:{agent.id}"
            replacement.supersedes_deliverable_id = predecessor.id
            side_effects: dict[str, Any] = {"resolved_blocker_ids": []}
            resolve_active_blockers_for_source(
                state,
                source_kind=TeamBlockerSourceKind.DELIVERABLE,
                source_id=predecessor.id,
                actor_agent_id=agent.id,
                dispatch_id=resolved.dispatch_id,
                note=f"Accepted replacement {replacement.id} was linked.",
                reference_ids=[replacement.id],
                result=side_effects,
                now=now,
            )
            for review_id in predecessor.review_request_ids:
                review = state.review_requests.get(review_id)
                if (
                    review is not None
                    and review.superseded_by_review_request_id is None
                    and review.retired_at is None
                ):
                    resolve_active_blockers_for_source(
                        state,
                        source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
                        source_id=review.id,
                        actor_agent_id=agent.id,
                        dispatch_id=resolved.dispatch_id,
                        note=f"Accepted replacement {replacement.id} was linked.",
                        reference_ids=[replacement.id],
                        result=side_effects,
                        now=now,
                    )
            _recalculate_project_status(state, project_id)
            return TeamCommandApplyResult(
                response_payload={
                    "intent_recorded": True,
                    "async_effect_pending": False,
                    "predecessor_deliverable_id": predecessor.id,
                    "replacement_deliverable_id": replacement.id,
                    "supersession_reason": normalized_payload["reason"],
                    "resolved_blocker_ids": list(side_effects.get("resolved_blocker_ids") or []),
                    "progress_made": True,
                },
                progress_classification="deliverable_lineage",
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
        )


__all__ = ["TeamDeliverableSubmissionCommandService"]
