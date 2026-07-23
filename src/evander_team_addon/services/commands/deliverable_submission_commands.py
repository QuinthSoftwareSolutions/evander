from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any

from evander.models import utc_now

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
    _validate_command_deliverable_has_acceptance_recipient,
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
    TeamBlockerSourceKind,
    TeamRecordState,
    TeamState,
    _unique_text_list,
    task_payload_with_derived,
)
from ..domain.common import (
    _optional_text,
    _required_text,
)
from ..domain.agent_access import _is_project_lead_for, _is_team_manager
from ..domain.blockers import resolve_active_blockers_for_source
from ..domain.deliverables import (
    _deliverable_submission_next_action_hints,
)
from ..domain.project_status import _recalculate_project_status
from ..domain.review import (
    _apply_review_request,
    _review_request_was_created,
)
from ..domain.work_due import (
    _mark_agent_work_due,
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
            _validate_command_deliverable_has_acceptance_recipient(agent)
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
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
                published_records=published_records,
                suppress_superior_notice=False,
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
                    "published_artifact_refs": published_refs,
                    "published_deliverable_artifacts": [
                        dict(record) for record in published_records
                    ],
                    "task_id": task.id,
                    "task_state": state.tasks[task.id].state.value,
                    "waiting_reason": task_payload_with_derived(state, task)["waiting_reason"],
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
            )
            if not normalized_reviews:
                raise TeamCommandValidationError(
                    code="empty_reviews",
                    message="reviews must contain at least one review request",
                    correction_context={"field": "reviews"},
                )
            planned_reviews = _plan_command_review_requests_for_deliverable(
                state,
                actor=agent,
                deliverable_id=deliverable_id,
                task=task,
                reviews=normalized_reviews,
                request_id=normalized_request_id,
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
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
                published_records=published_records,
                suppress_superior_notice=True,
            )
            review_result = {"errors": [], "created_inbox_item_ids": []}
            created_review_ids: list[str] = []
            for index, request_payload in enumerate(planned_reviews):
                created_id = _apply_review_request(
                    state,
                    actor=agent,
                    request=request_payload,
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
                    "waiting_reason": task_payload_with_derived(state, task)["waiting_reason"],
                    "intent_recorded": True,
                    "async_effect_pending": bool(created_inbox_ids),
                    "verification": normalized_deliverable.get("verification"),
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
            agent = _require_command_agent(
                resolved, "team_link_deliverable_replacement"
            )
            predecessor = state.deliverables.get(
                normalized_payload["predecessor_deliverable_id"]
            )
            replacement = state.deliverables.get(
                normalized_payload["replacement_deliverable_id"]
            )
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
            if not (
                _is_project_lead_for(state, agent.id, project_id)
                or _is_team_manager(state, agent)
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
                        f"replacement already supersedes "
                        f"{replacement.supersedes_deliverable_id}"
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
                    "resolved_blocker_ids": list(
                        side_effects.get("resolved_blocker_ids") or []
                    ),
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
