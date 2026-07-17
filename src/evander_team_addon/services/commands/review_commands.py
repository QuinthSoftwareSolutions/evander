from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any

from evander.models import utc_now

from .deliverable_helpers import (
    _command_review_request_id,
    _normalize_review_decision_mapping,
    _normalize_review_request_mapping,
    _review_decision_correction_context,
    _team_decide_review_normalized_payload,
    _team_request_review_normalized_payload,
)
from .executor import TeamCommandExecutor
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import (
    _require_command_agent,
    _require_command_agent_by_id,
    _validate_command_recipient_active,
)
from ...models import (
    TeamState,
    _unique_text_list,
    task_payload_with_derived,
)
from ..domain.agent_access import _is_team_manager
from ..domain.common import (
    _optional_text,
    _required_text,
)
from ..domain.manager_fallback import _manager_fallback_review_decision_guard_error
from ..domain.project_status import _recalculate_project_status
from ..domain.review import (
    _apply_review_decision,
    _apply_review_request,
    _review_request_was_created,
)
from ..domain.work_due import (
    _mark_agent_work_due,
)


class TeamReviewCommandService(TeamCommandExecutor):
    def request_review_command(
        self,
        *,
        session_id: str,
        deliverable_id: str,
        reviewer_agent_id: str,
        request_id: str,
        id: str | None = None,
        review_profile_id: str | None = None,
        instructions: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_review = _normalize_review_request_mapping(
            {
                "id": id,
                "deliverable_id": deliverable_id,
                "reviewer_agent_id": reviewer_agent_id,
                "review_profile_id": review_profile_id,
                "instructions": instructions,
            },
            field_name="review",
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_request_review",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="review_request",
        )
        normalized_payload = _team_request_review_normalized_payload(
            review=normalized_review,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_request_review")
            reviewer = _require_command_agent_by_id(
                state,
                normalized_review["reviewer_agent_id"],
                field_name="reviewer_agent_id",
            )
            _validate_command_recipient_active(reviewer)
            review_payload = dict(normalized_review)
            if review_payload.get("id") is None:
                review_payload["id"] = _command_review_request_id(
                    state,
                    deliverable_id=normalized_review["deliverable_id"],
                    review_request=normalized_review,
                    request_id=normalized_request_id,
                )
            review_result = {"errors": [], "created_inbox_item_ids": []}
            projects_to_recalc: set[str] = set()
            created_id = _apply_review_request(
                state,
                actor=agent,
                request=review_payload,
                result=review_result,
                projects_to_recalc=projects_to_recalc,
            )
            if created_id is None:
                raise TeamCommandValidationError(
                    code="review_request_failed",
                    message="; ".join(review_result["errors"])
                    or "review request could not be created",
                    correction_context={
                        "review": dict(normalized_review),
                        "errors": list(review_result["errors"]),
                    },
                )
            review = state.review_requests[created_id]
            for project_id in sorted(projects_to_recalc):
                _recalculate_project_status(state, project_id)
            existing_review_ids = list(review_result.get("existing_review_request_ids", []))
            created_review_ids = (
                [created_id] if _review_request_was_created(review_result, created_id) else []
            )
            if created_review_ids:
                _mark_agent_work_due(state, agent_id=review.reviewer_agent_id, now=utc_now())
            created_inbox_ids = list(review_result["created_inbox_item_ids"])
            if existing_review_ids:
                next_action_hints = [
                    "Review request already exists for this deliverable and reviewer; "
                    "wait for the assigned reviewer to decide it before treating the "
                    "deliverable or task as accepted."
                ]
            else:
                next_action_hints = [
                    "Review request created; wait for the assigned reviewer to "
                    "decide it before treating the deliverable or task as accepted."
                ]
            progress_made = bool(created_review_ids or normalized_handled_ids)
            return TeamCommandApplyResult(
                response_payload={
                    "review_request_id": created_id,
                    "created_review_request_ids": created_review_ids,
                    "existing_review_request_ids": existing_review_ids,
                    "created_inbox_item_ids": created_inbox_ids,
                    "events": list(review_result.get("events") or []),
                    "deliverable_id": review.deliverable_id,
                    "reviewer_agent_id": review.reviewer_agent_id,
                    "intent_recorded": True,
                    "async_effect_pending": bool(created_inbox_ids),
                    "next_action_hints": next_action_hints,
                    "progress_made": progress_made,
                },
                created_domain_ids=[*created_review_ids, *created_inbox_ids],
                progress_classification="review_request" if progress_made else "none",
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def decide_review_command(
        self,
        *,
        session_id: str,
        review_request_id: str,
        state: str,
        request_id: str,
        findings: Sequence[str] | None = None,
        criteria: Sequence[Mapping[str, Any]] | None = None,
        finding_resolutions: Sequence[Mapping[str, Any]] | None = None,
        override_routing_gap_reason: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_decision = _normalize_review_decision_mapping(
            {
                "review_request_id": review_request_id,
                "state": state,
                "findings": list(findings or []),
                "criteria": [dict(item) for item in criteria or []],
                "finding_resolutions": [dict(item) for item in finding_resolutions or []],
                "override_routing_gap_reason": override_routing_gap_reason,
            },
            field_name="review_decision",
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_decide_review",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="review_decision",
        )
        normalized_payload = _team_decide_review_normalized_payload(
            decision=normalized_decision,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            command_state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_decide_review")
            review = command_state.review_requests.get(normalized_decision["review_request_id"])
            if review is None:
                raise TeamCommandValidationError(
                    code="review_request_not_found",
                    message=(
                        "review decision references missing review request "
                        f"{normalized_decision['review_request_id']}"
                    ),
                    correction_context={
                        "review_request_id": normalized_decision["review_request_id"],
                    },
                )
            guard_error = (
                _manager_fallback_review_decision_guard_error(
                    command_state,
                    actor=agent,
                    review=review,
                )
                if _is_team_manager(command_state, agent)
                and review.release_package_id is None
                and resolved.runtime is not None
                and resolved.runtime.wake_reason == "manager_fallback"
                else None
            )
            override_reason = _optional_text(normalized_decision.get("override_routing_gap_reason"))
            if override_reason is not None and guard_error is None:
                raise TeamCommandValidationError(
                    code="override_routing_gap_not_required",
                    message=(
                        "override_routing_gap_reason is only allowed when current "
                        "manager-fallback routing rules require an override"
                    ),
                    correction_context=_review_decision_correction_context(
                        command_state,
                        review=review,
                        reviewer=command_state.agents.get(review.reviewer_agent_id),
                    ),
                )
            decision_result = {
                "errors": [],
                "created_inbox_item_ids": [],
                "events": [],
                "git_promotion_event_ids": [],
            }
            projects_to_recalc: set[str] = set()
            decided_id = _apply_review_decision(
                command_state,
                actor=agent,
                decision=normalized_decision,
                result=decision_result,
                projects_to_recalc=projects_to_recalc,
                enforce_manager_fallback_routing_guard=guard_error is not None,
            )
            if decided_id is None:
                raise TeamCommandValidationError(
                    code="review_decision_failed",
                    message="; ".join(decision_result["errors"])
                    or "review decision could not be recorded",
                    correction_context=_review_decision_correction_context(
                        command_state,
                        review=review,
                        reviewer=command_state.agents.get(review.reviewer_agent_id),
                    )
                    | {"errors": list(decision_result["errors"])},
                )
            for project_id in sorted(projects_to_recalc):
                _recalculate_project_status(command_state, project_id)
            decided_review = command_state.review_requests[decided_id]
            if decided_review.release_package_id is not None:
                release = command_state.release_packages[decided_review.release_package_id]
                return TeamCommandApplyResult(
                    response_payload={
                        "review_request_id": decided_id,
                        "decided_review_request_ids": [decided_id],
                        "created_inbox_item_ids": list(decision_result["created_inbox_item_ids"]),
                        "review": decided_review.model_dump(mode="json"),
                        "release_package": release.model_dump(mode="json"),
                        "release_package_id": release.id,
                        "events": list(decision_result.get("events") or []),
                        "intent_recorded": True,
                        "async_effect_pending": bool(
                            decision_result["created_inbox_item_ids"]
                            or decision_result.get("events")
                        ),
                        "progress_made": True,
                    },
                    created_domain_ids=[
                        decided_id,
                        *list(decision_result["created_inbox_item_ids"]),
                    ],
                )
            deliverable = command_state.deliverables[decided_review.deliverable_id]
            task = command_state.tasks[deliverable.task_id]
            return TeamCommandApplyResult(
                response_payload={
                    "review_request_id": decided_id,
                    "decided_review_request_ids": [decided_id],
                    "created_inbox_item_ids": list(decision_result["created_inbox_item_ids"]),
                    "review": decided_review.model_dump(mode="json"),
                    "deliverable": deliverable.model_dump(mode="json"),
                    "task": task_payload_with_derived(command_state, task),
                    "deliverable_id": deliverable.id,
                    "task_id": task.id,
                    "intent_recorded": True,
                    "async_effect_pending": bool(decision_result["created_inbox_item_ids"]),
                    "progress_made": True,
                },
                created_domain_ids=[
                    decided_id,
                    *list(decision_result["created_inbox_item_ids"]),
                ],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


__all__ = ["TeamReviewCommandService"]
